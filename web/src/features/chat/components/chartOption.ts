/**
 * 结果表画成图：表格卡片里工具给的「怎么画」（TableChart）加上用户选的图形与数值列，拼成 ECharts 的配置。
 * 纯函数，不碰 DOM（颜色、字体由调用方从组件库的设计变量里取好传进来）。
 *
 * - 几列数值按工具给的分组画（groups，问数按单位、量级分）：同一组的画在一张图里（营收和目标两条线），组之间在上面
 *   切换；量级差很远的（金额与单数）不同组，画在一根轴上小的那个就看不见了。没给分组时一列一组；
 * - 柱子朝向照通行做法（2026-11-04 用户报「柱状图都是横的」，原来分类一律横着画）：时间（日期列，或年份、季度、
 *   月份这种写法）、按组分线、少而短的分类竖着画；分类多（超过 8 个）或名字长（竖着放标签得斜着排）的横着画，
 *   数写在柱子尾巴上、不画数值轴；
 * - 几条线的图例不在图里画，界面在图上面那一排自己写（seriesNames、dataColors），和切换图形的控件放一排；
 * - 颜色（2026-11-04 照设计稿）：一组数据用蓝；几条线、饼图的几块依次用组件库色板里的蓝、绿、橙、紫、粉、黄，
 *   不用红（红表示出错）；色板只有六种，第七、八组用两档灰。
 */
import type { ChartTokenValues } from '../../../ui/chartTokens';
import type { CardCell, TableCard, TableChart } from '../events/types';

export type ChartKind = 'line' | 'area' | 'bar' | 'pie';

export type ChartTokens = ChartTokenValues;

/** 饼图最多几块：再多就分不清了（同成熟问数产品的门槛） */
export const PIE_MAX = 7;
/** 按 series 分线最多几条 */
export const SERIES_MAX = 8;

const label = (value: CardCell) => (value === null ? '（空）' : String(value));
const numberOf = (value: CardCell): number | null =>
  typeof value === 'number'
    ? value
    : typeof value === 'string' && value.trim() !== '' && !Number.isNaN(Number(value))
      ? Number(value)
      : null;

/** 轴上的刻度写短：万、亿 */
export function compact(value: number): string {
  const abs = Math.abs(value);
  if (abs >= 1e8) return `${trim(value / 1e8)}亿`;
  if (abs >= 1e4) return `${trim(value / 1e4)}万`;
  return trim(value);
}

/** 悬停写全：千分位，最多两位小数 */
export const full = (value: number) => value.toLocaleString('zh-CN', { maximumFractionDigits: 2 });

function trim(value: number): string {
  return Number(value.toFixed(2)).toLocaleString('zh-CN', { maximumFractionDigits: 2 });
}

/** 画数据用的颜色：色板依次用完，再用两档灰（ECharts 按顺序给每条线、每一块取一个） */
export const dataColors = (tokens: ChartTokens): string[] => [
  ...tokens.palette,
  tokens.tertiary,
  tokens.secondary,
];

const columnIndex = (table: TableCard, name: string | undefined) =>
  name === undefined ? -1 : table.columns.findIndex((c) => c.name === name);

/** 几列数值的分组：同一组的画在一张图里，组之间切换；没分组、按组分线时一列一组 */
export function measureGroups(chart: TableChart): string[][] {
  if (chart.series || !chart.groups) return chart.y.map((name) => [name]);
  return chart.groups.map((group) => [...group]);
}

/** 这一列所在的那一组；对不上（看板记着的旧列名）的是第一组。界面用组里第一列的列名认一组 */
export function groupOf(chart: TableChart, name: string | undefined): string[] {
  const groups = measureGroups(chart);
  return groups.find((group) => name !== undefined && group.includes(name)) ?? groups[0] ?? [];
}

/** 这张表能切成哪几种图：时间序列折线、面积、柱状；分类柱状，单列非负、块数不多时还能画饼（一组几列的不画饼） */
export function compatibleKinds(table: TableCard, chart: TableChart, measure: string): ChartKind[] {
  const x = columnIndex(table, chart.x);
  const xIsTime = table.columns[x]?.kind === 'date';
  if (chart.series || xIsTime || chart.type === 'line') return ['line', 'area', 'bar'];
  if (groupOf(chart, measure).length > 1) return ['bar'];
  const y = columnIndex(table, measure);
  const values = table.rows.map((row) => numberOf(row[y] ?? null));
  const pieable =
    table.rows.length <= PIE_MAX &&
    values.every((v) => v !== null && v >= 0) &&
    values.some((v) => (v ?? 0) > 0);
  // 分类没有先后（区域、门店），连成折线看着像有趋势，不给
  return pieable ? ['bar', 'pie'] : ['bar'];
}

/** 现在画哪种：用户选的这一列能画就画它，不能（换了一列、饼图画不了了）就画能画的第一种 */
export function shownKind(
  table: TableCard,
  chart: TableChart,
  measure: string,
  picked: ChartKind,
): ChartKind {
  const kinds = compatibleKinds(table, chart, measure);
  return kinds.includes(picked) ? picked : (kinds[0] ?? 'bar');
}

/** 竖着放得下的分类：不超过这么多个，名字不超过这么宽（一个汉字算 2，英文、数字算 1） */
const COLUMN_MAX = 8;
const LABEL_WIDTH_MAX = 10;
/** 写成时间的分类：2018、2026-07、2026年7月、2026 Q3、7月、第三季度 */
const TIME_LIKE =
  /^(?:\d{4}(?:\s*年)?(?:[-/.]\d{1,2}|\s*\d{1,2}\s*月|\s*[Qq][1-4]|\s*第?[一二三四1-4]季度)?|\d{1,2}\s*月|[Qq][1-4]|第?[一二三四1-4]季度)$/;

const widthOf = (text: string) =>
  [...text].reduce((sum, char) => sum + ((char.codePointAt(0) ?? 0) > 0xff ? 2 : 1), 0);

/** 柱子横着画：分类多或名字长；时间、按组分线、少而短的分类竖着画 */
export function isHorizontal(table: TableCard, chart: TableChart, kind: ChartKind): boolean {
  if (kind !== 'bar' || chart.series) return false;
  const x = columnIndex(table, chart.x);
  if (table.columns[x]?.kind === 'date') return false;
  const labels = table.rows.map((row) => label(row[x] ?? null).trim());
  if (labels.length > 0 && labels.every((text) => TIME_LIKE.test(text))) return false;
  return labels.length > COLUMN_MAX || labels.some((text) => widthOf(text) > LABEL_WIDTH_MAX);
}

/** 图多高：横着的柱子按分类数算（每根 36px，三五根不必撑满一大块），其余固定 260 */
/** 横向柱状一根一行（BAR_ROW 高），不封顶：封顶的话几十个分类挤成细线，数和名字叠在一起；超过 CHART_VIEW_MAX
 * 的在卡片里滚动（ResultChart） */
const BAR_ROW = 36;
export const CHART_VIEW_MAX = 420;

export function chartHeight(table: TableCard, chart: TableChart, kind: ChartKind): number {
  if (!isHorizontal(table, chart, kind)) return 260;
  return Math.max(120, table.rows.length * BAR_ROW + 24);
}

/** 图上点的那一格（分类名）对应横轴那一列的原始取值；合并出来的、对不上的是 undefined */
export function pickedValue(
  table: TableCard,
  chart: TableChart,
  name: string,
): { readonly value: CardCell } | undefined {
  const x = columnIndex(table, chart.x);
  const row = table.rows.find((r) => label(r[x] ?? null) === name);
  return row && x >= 0 ? { value: row[x] ?? null } : undefined;
}

/** 工具给的类型落成默认的那一种 */
export const defaultKind = (chart: TableChart): ChartKind => chart.type;

interface Built {
  readonly categories: string[];
  readonly series: { readonly name: string; readonly values: (number | null)[] }[];
}

/** 按 series 分成的几条线叫什么（按出现的先后，最多 SERIES_MAX 条）；没有 series 是空的 */
export function seriesNames(table: TableCard, chart: TableChart): string[] {
  const s = columnIndex(table, chart.series);
  if (s < 0) return [];
  return [...new Set(table.rows.map((row) => label(row[s] ?? null)))].slice(0, SERIES_MAX);
}

/** 取数：没有 series 时这一组的每列一条；有 series 按它的取值分成几条，横轴按出现的先后 */
function build(table: TableCard, chart: TableChart, measure: string): Built {
  const x = columnIndex(table, chart.x);
  const y = columnIndex(table, measure);
  const s = columnIndex(table, chart.series);
  if (s < 0)
    return {
      categories: table.rows.map((row) => label(row[x] ?? null)),
      series: groupOf(chart, measure).map((name) => {
        const i = columnIndex(table, name);
        return {
          name: table.columns[i]?.label ?? name,
          values: table.rows.map((row) => numberOf(row[i] ?? null)),
        };
      }),
    };
  const categories = [...new Set(table.rows.map((row) => label(row[x] ?? null)))];
  const names = seriesNames(table, chart);
  return {
    categories,
    series: names.map((name) => {
      const byX = new Map(
        table.rows
          .filter((row) => label(row[s] ?? null) === name)
          .map((row) => [label(row[x] ?? null), numberOf(row[y] ?? null)] as const),
      );
      return { name, values: categories.map((c) => byX.get(c) ?? null) };
    }),
  };
}

const baseStyle = (tokens: ChartTokens) => {
  const textStyle = { fontFamily: tokens.font, fontSize: 12, color: tokens.secondary };
  return {
    color: dataColors(tokens),
    textStyle,
    tooltip: {
      backgroundColor: tokens.surface,
      borderColor: tokens.border,
      textStyle: { ...textStyle, color: tokens.text },
      valueFormatter: (value: unknown) => (typeof value === 'number' ? full(value) : '—'),
    },
  };
};

/**
 * 饼图右边图例写全（名字、数、占比）要的宽度：环心在 29%、外圈 76%，图例从 58% 起写三栏，比这窄环的左边就出了画布、
 * 图例的数和占比也被截掉（网页嵌入的小窗、手机上的卡片只有 300 来宽）。再窄换紧凑排法，用 ECharts 自带的
 * media（按图的宽度套用，跟着容器宽度变）。
 */
const PIE_FULL_WIDTH = 510;

/** 图例里的名字按显示宽度截短（汉字算一格，字母数字算半格多），超了留几格加省略号；悬停图例看全名 */
function clip(name: string, cells: number): string {
  const width = (ch: string) => ((ch.codePointAt(0) ?? 0) <= 0xff ? 0.6 : 1);
  if ([...name].reduce((sum, ch) => sum + width(ch), 0) <= cells) return name;
  let used = 0;
  let kept = '';
  for (const ch of name) {
    if (used + width(ch) > cells - 1) break;
    used += width(ch);
    kept += ch;
  }
  return `${kept}…`;
}

/** 环形饼图：中间写合计与指标名，右边一列图例写每块的数与占比；卡片窄时环小一点、图例只写名字和占比 */
function pieOption(built: Built, measureLabel: string, tokens: ChartTokens): Record<string, unknown> {
  const base = baseStyle(tokens);
  const values = built.series[0]?.values ?? [];
  const data = built.categories.map((name, i) => ({ name, value: values[i] ?? 0 }));
  const total = data.reduce((sum, d) => sum + d.value, 0);
  const byName = new Map(data.map((d) => [d.name, d.value]));
  const percent = (value: number) => (total > 0 ? `${Math.round((value / total) * 100)}%` : '—');
  const narrow = {
    title: { left: '28%', textStyle: { fontSize: 18 } },
    legend: {
      left: '56%',
      formatter: (name: string) => `{name|${clip(name, 4)}}{pct|${percent(byName.get(name) ?? 0)}}`,
      textStyle: { rich: { name: { width: 56 }, pct: { width: 36 } } },
    },
    series: [{ center: ['28%', '50%'], radius: ['36%', '54%'] }],
  };
  return {
    ...base,
    tooltip: { ...base.tooltip, trigger: 'item' },
    title: {
      text: compact(total),
      subtext: measureLabel,
      left: '29%',
      top: 'middle',
      textAlign: 'center',
      itemGap: 2,
      textStyle: { fontFamily: tokens.font, fontSize: 20, fontWeight: 600, color: tokens.text },
      subtextStyle: { fontFamily: tokens.font, fontSize: 11, color: tokens.tertiary },
    },
    legend: {
      orient: 'vertical',
      left: '58%',
      top: 'middle',
      icon: 'roundRect',
      itemWidth: 10,
      itemHeight: 10,
      itemGap: 10,
      tooltip: { show: true },
      formatter: (name: string) => {
        const value = byName.get(name) ?? 0;
        return `{name|${clip(name, 7)}}{value|${full(value)}}{pct|${percent(value)}}`;
      },
      textStyle: {
        ...base.textStyle,
        fontSize: 13,
        rich: {
          name: { width: 96, color: tokens.text, fontSize: 13 },
          value: { width: 56, align: 'right', color: tokens.text, fontSize: 13 },
          pct: { width: 44, align: 'right', color: tokens.tertiary, fontSize: 13 },
        },
      },
    },
    series: [
      {
        type: 'pie',
        center: ['29%', '50%'],
        radius: ['50%', '76%'],
        itemStyle: { borderColor: tokens.surface, borderWidth: 2 },
        label: { show: false },
        data,
      },
    ],
    media: [{ query: { maxWidth: PIE_FULL_WIDTH - 1 }, option: narrow }],
  };
}

/** 柱子横着画时：数写在柱子尾巴上，不画数值轴 */
function barStyle(horizontal: boolean, tokens: ChartTokens) {
  return {
    barMaxWidth: horizontal ? 22 : 28,
    itemStyle: { borderRadius: horizontal ? [0, 4, 4, 0] : [3, 3, 0, 0] },
    ...(horizontal
      ? {
          label: {
            show: true,
            position: 'right',
            color: tokens.text,
            fontSize: 13,
            formatter: ({ value }: { readonly value: unknown }) =>
              typeof value === 'number' ? compact(value) : '',
          },
        }
      : {}),
  };
}

export function chartOption(
  table: TableCard,
  chart: TableChart,
  {
    kind,
    measure,
    tokens,
  }: { readonly kind: ChartKind; readonly measure: string; readonly tokens: ChartTokens },
): Record<string, unknown> {
  const built = build(table, chart, measure);
  if (kind === 'pie') return pieOption(built, built.series[0]?.name ?? measure, tokens);
  const { categories, series } = built;
  const base = baseStyle(tokens);
  const horizontal = isHorizontal(table, chart, kind);
  const negative = series.some((s) => s.values.some((v) => v !== null && v < 0));
  const category = {
    type: 'category',
    data: categories,
    axisLine: { show: !horizontal, lineStyle: { color: tokens.border } },
    axisTick: { show: false },
    axisLabel: horizontal
      ? { color: tokens.secondary, fontSize: 13 }
      : { color: tokens.tertiary, hideOverlap: true },
    ...(horizontal ? { inverse: true } : {}),
  };
  const value = {
    type: 'value',
    show: !horizontal,
    splitLine: { lineStyle: { color: tokens.border, type: 'dashed' } },
    axisLabel: { color: tokens.tertiary, formatter: compact },
  };
  return {
    ...base,
    tooltip: { ...base.tooltip, trigger: 'axis', axisPointer: { type: kind === 'bar' ? 'shadow' : 'line' } },
    grid: {
      left: horizontal && negative ? 60 : 4,
      right: horizontal ? 56 : 16,
      top: 12,
      bottom: 4,
      containLabel: true,
    },
    xAxis: horizontal ? value : category,
    yAxis: horizontal ? category : value,
    series: series.map((s) => ({
      name: s.name,
      type: kind === 'area' ? 'line' : kind,
      // 横着画时负的那根往左长，数写在它左边那头（写右边就压在零线上、挨着别的柱子）
      data:
        kind === 'bar' && horizontal
          ? s.values.map((v) => (v !== null && v < 0 ? { value: v, label: { position: 'left' } } : v))
          : s.values,
      ...(kind === 'bar'
        ? barStyle(horizontal, tokens)
        : {
            symbol: 'circle',
            symbolSize: 5,
            showSymbol: categories.length <= 31,
            connectNulls: false,
            lineStyle: { width: 2 },
          }),
      ...(kind === 'area' ? { areaStyle: { opacity: 0.12 } } : {}),
    })),
  };
}
