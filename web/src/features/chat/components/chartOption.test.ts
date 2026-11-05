import { describe, expect, it } from 'vitest';
import type { TableCard } from '../events/types';
import {
  chartHeight,
  isHorizontal,
  chartOption,
  compact,
  compatibleKinds,
  dataColors,
  seriesNames,
  type ChartTokens,
} from './chartOption';

/** 测试里要看的那几项（配置本身是 ECharts 的宽松对象） */
interface Axis {
  readonly type: string;
  readonly show?: boolean;
  readonly data?: readonly string[];
  readonly inverse?: boolean;
  readonly axisLabel: { readonly formatter: (value: number) => string };
}
interface Shape {
  readonly xAxis: Axis;
  readonly yAxis: Axis;
  readonly color: readonly string[];
  readonly legend?: { readonly formatter: (name: string) => string };
  readonly title?: { readonly text: string; readonly subtext: string };
  readonly tooltip: { readonly valueFormatter: (value: unknown) => string };
  readonly series: readonly {
    readonly name: string;
    readonly type: string;
    readonly data: unknown;
    readonly areaStyle?: unknown;
    readonly label?: { readonly show?: boolean; readonly formatter?: unknown };
  }[];
}
const shape = (option: Record<string, unknown>) => option as unknown as Shape;

const TOKENS: ChartTokens = {
  text: 'rgb(13, 13, 13)',
  secondary: 'rgb(93, 93, 93)',
  tertiary: 'rgb(143, 143, 143)',
  border: 'rgba(13, 13, 13, 0.08)',
  surface: 'rgb(255, 255, 255)',
  font: 'system-ui',
  palette: ['#0285ff', '#00a240', '#e25507', '#8046d9', '#e04c91', '#e0ac00'],
};

const trend: TableCard = {
  type: 'table',
  columns: [
    { name: 'month', label: '月份', kind: 'date' },
    { name: 'revenue', label: '销售额', kind: 'number' },
    { name: 'orders', label: '订单数', kind: 'number' },
  ],
  rows: [
    ['2026-01', 120000, 30],
    ['2026-02', 98000, 25],
    ['2026-03', null, 0],
  ],
  rowCount: 3,
  moreRows: false,
  chart: { type: 'line', x: 'month', y: ['revenue', 'orders'] },
};

const regions: TableCard = {
  type: 'table',
  columns: [
    { name: 'region', label: '区域', kind: 'text' },
    { name: 'revenue', label: '销售额', kind: 'number' },
  ],
  rows: [
    ['华东', 300],
    ['华南', 200],
    ['华北', 100],
  ],
  rowCount: 3,
  moreRows: false,
  chart: { type: 'bar', x: 'region', y: ['revenue'] },
};

describe('结果表画成图的配置', () => {
  it('时间序列画折线：横轴是时间，一次画一列数值，空值断开；悬停写全数，轴上写万', () => {
    const option = shape(
      chartOption(trend, trend.chart!, {
        kind: 'line',
        measure: 'revenue',
        tokens: TOKENS,
      }),
    );
    expect(option.xAxis.data).toEqual(['2026-01', '2026-02', '2026-03']);
    expect(option.series).toHaveLength(1);
    expect(option.series[0]).toMatchObject({ name: '销售额', type: 'line', data: [120000, 98000, null] });
    expect(option.yAxis.axisLabel.formatter(120000)).toBe('12万');
    expect(option.tooltip.valueFormatter(98000.456)).toBe('98,000.46');
    const orders = shape(
      chartOption(trend, trend.chart!, {
        kind: 'area',
        measure: 'orders',
        tokens: TOKENS,
      }),
    );
    expect(orders.series[0]).toMatchObject({ name: '订单数', type: 'line', data: [30, 25, 0] });
    expect(orders.series[0]?.areaStyle).toBeDefined();
  });

  it('柱子朝向照通行做法：少而短的分类、年份这类竖着画；分类多或名字长的横着画（分类在纵轴、从上往下按原顺序）', () => {
    const few = shape(
      chartOption(regions, regions.chart!, { kind: 'bar', measure: 'revenue', tokens: TOKENS }),
    );
    expect(few.xAxis).toMatchObject({ type: 'category', data: ['华东', '华南', '华北'] });
    expect(chartHeight(regions, regions.chart!, 'bar')).toBe(260);
    const years: TableCard = {
      ...regions,
      rows: [
        ['2018', 3],
        ['2019', 4],
        ['2020', 5],
      ],
    };
    expect(isHorizontal(years, regions.chart!, 'bar')).toBe(false);
    const months: TableCard = {
      ...regions,
      rows: [
        ['2026年7月', 3],
        ['2026年8月', 4],
      ],
    };
    expect(isHorizontal(months, regions.chart!, 'bar')).toBe(false);
    const ranking: TableCard = {
      ...regions,
      rows: Array.from({ length: 10 }, (_, i) => [`客户${i + 1}`, 100 - i]),
      rowCount: 10,
    };
    const bar = shape(
      chartOption(ranking, regions.chart!, { kind: 'bar', measure: 'revenue', tokens: TOKENS }),
    );
    expect(bar.yAxis).toMatchObject({ type: 'category', inverse: true });
    expect(bar.yAxis.data?.slice(0, 2)).toEqual(['客户1', '客户2']);
    expect(bar.xAxis).toMatchObject({ type: 'value', show: false });
    expect(bar.series[0]?.label?.show).toBe(true);
    const formatter = bar.series[0]?.label?.formatter as (p: { value: unknown }) => string;
    expect(formatter({ value: 1_286_000 })).toBe('128.6万');
    expect(chartHeight(ranking, regions.chart!, 'bar')).toBe(384);
    const long: TableCard = {
      ...regions,
      rows: [
        ['华东区域直营渠道', 3],
        ['华南', 2],
      ],
    };
    expect(isHorizontal(long, regions.chart!, 'bar')).toBe(true);
    const byMonth = shape(
      chartOption(trend, trend.chart!, { kind: 'bar', measure: 'revenue', tokens: TOKENS }),
    );
    expect(byMonth.xAxis.type).toBe('category');
    expect(chartHeight(trend, trend.chart!, 'bar')).toBe(260);
  });

  it('按分组分成几条线：横轴按出现的先后，某组缺的那个点是空；颜色依次取色板，图例由界面画', () => {
    const grouped: TableCard = {
      ...trend,
      columns: [trend.columns[0]!, { name: 'region', label: '区域' }, trend.columns[1]!],
      rows: [
        ['2026-01', '华东', 10],
        ['2026-01', '华南', 5],
        ['2026-02', '华东', 12],
      ],
      chart: { type: 'line', x: 'month', y: ['revenue'], series: 'region' },
    };
    const option = shape(
      chartOption(grouped, grouped.chart!, {
        kind: 'line',
        measure: 'revenue',
        tokens: TOKENS,
      }),
    );
    expect(option.xAxis.data).toEqual(['2026-01', '2026-02']);
    expect(option.series.map((s) => [s.name, s.data])).toEqual([
      ['华东', [10, 12]],
      ['华南', [5, null]],
    ]);
    expect(option.legend).toBeUndefined();
    expect(seriesNames(grouped, grouped.chart!)).toEqual(['华东', '华南']);
    expect(option.color.slice(0, 2)).toEqual(['#0285ff', '#00a240']);
  });

  it('两列分类画分组柱状：横轴是一列分类、按另一列分组，竖着画；空的分类写「（空）」', () => {
    const grouped: TableCard = {
      type: 'table',
      columns: [
        { name: 'region', label: '地区' },
        { name: 'level', label: '客户等级' },
        { name: 'orders', label: '订单数', kind: 'number' },
      ],
      rows: [
        ['华东', 'VIP', 3],
        ['华南', 'VIP', 2],
        ['华东', 'normal', 2],
        [null, 'normal', 1],
      ],
      rowCount: 4,
      moreRows: false,
      chart: { type: 'bar', x: 'region', y: ['orders'], series: 'level' },
    };
    const option = shape(
      chartOption(grouped, grouped.chart!, { kind: 'bar', measure: 'orders', tokens: TOKENS }),
    );
    expect(isHorizontal(grouped, grouped.chart!, 'bar')).toBe(false);
    expect(option.xAxis.data).toEqual(['华东', '华南', '（空）']);
    expect(option.series.map((s) => [s.name, s.type, s.data])).toEqual([
      ['VIP', 'bar', [3, 2, null]],
      ['normal', 'bar', [2, null, 1]],
    ]);
  });

  it('能切的图：时间序列折线、面积、柱状；分类单列不为负、块数不多还能画饼，饼图带百分比', () => {
    expect(compatibleKinds(trend, trend.chart!, 'revenue')).toEqual(['line', 'area', 'bar']);
    expect(compatibleKinds(regions, regions.chart!, 'revenue')).toEqual(['bar', 'pie', 'line']);
    const negative = { ...regions, rows: [...regions.rows, ['西北', -5]] } as TableCard;
    expect(compatibleKinds(negative, regions.chart!, 'revenue')).toEqual(['bar', 'line']);
    const pie = shape(
      chartOption(regions, regions.chart!, {
        kind: 'pie',
        measure: 'revenue',
        tokens: TOKENS,
      }),
    );
    expect(pie.series[0]?.data).toEqual([
      { name: '华东', value: 300 },
      { name: '华南', value: 200 },
      { name: '华北', value: 100 },
    ]);
    expect(pie.title).toMatchObject({ text: '600', subtext: '销售额' });
    expect(pie.legend?.formatter('华东')).toBe('{name|华东}{value|300}{pct|50%}');
  });

  it('刻度写短；色板用完再用两档灰，不用红', () => {
    expect([compact(950), compact(12_500), compact(310_000_000), compact(-20_000)]).toEqual([
      '950',
      '1.25万',
      '3.1亿',
      '-2万',
    ]);
    expect(dataColors(TOKENS)).toEqual([...TOKENS.palette, TOKENS.tertiary, TOKENS.secondary]);
  });
});
