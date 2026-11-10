/**
 * 结果表画成的图（表格卡片带了「怎么画」时，ResultCards 默认显示它，可以切回表）。2026-11-04 照设计稿：
 * - 换成兼容的哪种图（折线、面积、柱状、饼图）：只有一列数值、不分组时放在卡片头里，和「图 / 表」并排；
 * - 几个指标时图上面一排，右边换图形；左边切换画哪一组（同一组的画在一起，groups），这一组有几列、或按组分成
 *   几条线时左边还有图例；
 * - 图用 ECharts（ui/EChart，按需加载）；卡片里只带了前几行时，图下面写明只画了这几行；
 * - 给了 onPick（横轴那一列能下钻）时，点柱、点、扇区报出它在横轴那一列的原始取值。
 * 卡片头由 ResultCards 给（head），这里把要放进头里的控件交给它。
 */
import { lazy, Suspense, useMemo, useState, type ReactNode } from 'react';
import { SegmentedControl } from '@openai/apps-sdk-ui/components/SegmentedControl';
import { useChartTokens } from '../../../ui/chartTokens';
import type { CardCell, TableCard, TableChart } from '../events/types';
import {
  chartHeight,
  chartOption,
  CHART_VIEW_MAX,
  compatibleKinds,
  dataColors,
  defaultKind,
  groupOf,
  measureGroups,
  pickedValue,
  seriesNames,
  shownKind,
  type ChartKind,
} from './chartOption';

const EChart = lazy(() => import('../../../ui/EChart'));

const KIND_NAMES: Readonly<Record<ChartKind, string>> = {
  line: '折线图',
  area: '面积图',
  bar: '柱状图',
  pie: '饼图',
};
const KIND_LABELS: Readonly<Record<ChartKind, string>> = {
  line: '折线',
  area: '面积',
  bar: '柱状',
  pie: '饼图',
};

interface Props {
  readonly table: TableCard;
  readonly chart: TableChart;
  /** 画卡片头，参数是要放进头里的控件（没有就是 null） */
  readonly head: (controls: ReactNode) => ReactNode;
  /** 一开始画哪一列、哪种图形（看板记着的）；对不上这张表的不认 */
  readonly initial?: { readonly type?: ChartKind; readonly metric?: string };
  /** 换了画哪一列、哪种图形时调 */
  readonly onChange?: (picked: { readonly type: ChartKind; readonly metric: string }) => void;
  /** 点了图上的一项：横轴那一列的原始取值 */
  readonly onPick?: (value: CardCell) => void;
}

export function ResultChart({ table, chart, head, initial, onChange, onPick }: Props) {
  const tokens = useChartTokens();
  // 画哪一组，用组里第一列的列名认；看板记着的是组里别的列也认得
  const [measure, setMeasure] = useState(groupOf(chart, initial?.metric)[0] ?? '');
  const groups = measureGroups(chart);
  const group = groupOf(chart, measure);
  const kinds = compatibleKinds(table, chart, measure);
  const [picked, setPicked] = useState<ChartKind>(initial?.type ?? defaultKind(chart));
  const kind = shownKind(table, chart, measure, picked);
  const option = useMemo(
    () => chartOption(table, chart, { kind, measure, tokens }),
    [table, chart, kind, measure, tokens],
  );
  const height = chartHeight(table, chart, kind);
  const labelOf = (name: string) => table.columns.find((c) => c.name === name)?.label ?? name;
  const groupLabel = (names: readonly string[]) => names.map(labelOf).join('、');
  const legend =
    chart.series !== undefined ? seriesNames(table, chart) : group.length > 1 ? group.map(labelOf) : [];
  const kindSwitch =
    kinds.length > 1 ? (
      <SegmentedControl
        aria-label="图形"
        size="xs"
        value={kind}
        onChange={(next) => {
          setPicked(next);
          onChange?.({ type: next, metric: measure });
        }}
      >
        {kinds.map((k) => (
          <SegmentedControl.Option key={k} value={k}>
            {KIND_LABELS[k]}
          </SegmentedControl.Option>
        ))}
      </SegmentedControl>
    ) : null;
  const toolbar = groups.length > 1 || legend.length > 0;
  return (
    <>
      {head(toolbar ? null : kindSwitch)}
      <div className="kw-result-chart">
        {toolbar ? (
          <div className="kw-result-chart-bar">
            <div className="kw-result-chart-pick">
              {groups.length > 1 ? (
                <MeasureSwitch
                  groups={groups}
                  value={measure}
                  onChange={(next) => {
                    setMeasure(next);
                    onChange?.({ type: shownKind(table, chart, next, picked), metric: next });
                  }}
                  labelOf={groupLabel}
                />
              ) : null}
              {legend.length > 0 ? <Legend names={legend} colors={dataColors(tokens)} /> : null}
            </div>
            {kindSwitch}
          </div>
        ) : null}
        <div className={height > CHART_VIEW_MAX ? 'kw-echart-scroll' : undefined}>
          <Suspense fallback={<div className="kw-echart" style={{ height }} aria-hidden="true" />}>
            <EChart
              option={option}
              height={height}
              label={`${groupLabel(group)}按${labelOf(chart.x)}的${KIND_NAMES[kind]}`}
              onPick={
                onPick
                  ? (name) => {
                      const found = pickedValue(table, chart, name);
                      if (found) onPick(found.value);
                    }
                  : undefined
              }
            />
          </Suspense>
        </div>
        {table.rowCount > table.rows.length ? (
          <p className="kw-result-chart-note">图里只画了前 {table.rows.length} 行</p>
        ) : null}
      </div>
    </>
  );
}

/** 切换画哪一组：一组用它第一列的列名认，名字写组里几列的标签 */
function MeasureSwitch({
  groups,
  value,
  onChange,
  labelOf,
}: {
  readonly groups: readonly (readonly string[])[];
  readonly value: string;
  readonly onChange: (name: string) => void;
  readonly labelOf: (names: readonly string[]) => string;
}) {
  return (
    <SegmentedControl aria-label="画哪一列" size="xs" pill value={value} onChange={onChange}>
      {groups.map((group) => (
        <SegmentedControl.Option key={group[0]} value={group[0] ?? ''}>
          {labelOf(group)}
        </SegmentedControl.Option>
      ))}
    </SegmentedControl>
  );
}

/** 几条线的图例：颜色与图里同一顺序（dataColors） */
function Legend({
  names,
  colors,
}: {
  readonly names: readonly string[];
  readonly colors: readonly string[];
}) {
  return (
    <ul className="kw-result-legend" aria-label="图例">
      {names.map((name, i) => (
        <li key={name}>
          <span className="kw-result-legend-mark" style={{ background: colors[i % colors.length] }} />
          {name}
        </li>
      ))}
    </ul>
  );
}
