/**
 * 结果表画成的图（表格卡片带了「怎么画」时，ResultCards 默认显示它，可以切回表）。2026-11-04 照设计稿：
 * - 换成兼容的哪种图（折线、面积、柱状、饼图）：只有一列数值、不分组时放在卡片头里，和「图 / 表」并排；
 * - 几个指标时图上面一排，左边切换画哪一列，右边换图形；按组分成几条线时左边是图例，右边换图形；
 * - 图用 ECharts（ui/EChart，按需加载）；卡片里只带了前几行时，图下面写明只画了这几行。
 * 卡片头由 ResultCards 给（head），这里把要放进头里的控件交给它。
 */
import { lazy, Suspense, useMemo, useState, type ReactNode } from 'react';
import { SegmentedControl } from '@openai/apps-sdk-ui/components/SegmentedControl';
import { useChartTokens } from '../../../ui/chartTokens';
import type { TableCard, TableChart } from '../events/types';
import {
  chartHeight,
  chartOption,
  compatibleKinds,
  dataColors,
  defaultKind,
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
}

export function ResultChart({ table, chart, head }: Props) {
  const tokens = useChartTokens();
  const [measure, setMeasure] = useState(chart.y[0] ?? '');
  const kinds = compatibleKinds(table, chart, measure);
  const [picked, setPicked] = useState<ChartKind>(defaultKind(chart));
  const kind = shownKind(table, chart, measure, picked);
  const option = useMemo(
    () => chartOption(table, chart, { kind, measure, tokens }),
    [table, chart, kind, measure, tokens],
  );
  const height = chartHeight(table, chart, kind);
  const labelOf = (name: string) => table.columns.find((c) => c.name === name)?.label ?? name;
  const kindSwitch =
    kinds.length > 1 ? (
      <SegmentedControl aria-label="图形" size="xs" value={kind} onChange={setPicked}>
        {kinds.map((k) => (
          <SegmentedControl.Option key={k} value={k}>
            {KIND_LABELS[k]}
          </SegmentedControl.Option>
        ))}
      </SegmentedControl>
    ) : null;
  const toolbar = chart.y.length > 1 || chart.series !== undefined;
  return (
    <>
      {head(toolbar ? null : kindSwitch)}
      <div className="kw-result-chart">
        {toolbar ? (
          <div className="kw-result-chart-bar">
            {chart.y.length > 1 ? (
              <MeasureSwitch names={chart.y} value={measure} onChange={setMeasure} labelOf={labelOf} />
            ) : (
              <Legend names={seriesNames(table, chart)} colors={dataColors(tokens)} />
            )}
            {kindSwitch}
          </div>
        ) : null}
        <Suspense fallback={<div className="kw-echart" style={{ height }} aria-hidden="true" />}>
          <EChart
            option={option}
            height={height}
            label={`${labelOf(measure)}按${labelOf(chart.x)}的${KIND_NAMES[kind]}`}
          />
        </Suspense>
        {table.rowCount > table.rows.length ? (
          <p className="kw-result-chart-note">图里只画了前 {table.rows.length} 行</p>
        ) : null}
      </div>
    </>
  );
}

function MeasureSwitch({
  names,
  value,
  onChange,
  labelOf,
}: {
  readonly names: readonly string[];
  readonly value: string;
  readonly onChange: (name: string) => void;
  readonly labelOf: (name: string) => string;
}) {
  return (
    <SegmentedControl aria-label="画哪一列" size="xs" pill value={value} onChange={onChange}>
      {names.map((name) => (
        <SegmentedControl.Option key={name} value={name}>
          {labelOf(name)}
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
