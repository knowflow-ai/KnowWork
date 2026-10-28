/**
 * 按天的趋势折线（运营大盘用）。组件库没有图表，DSH 官方和社区也没有现成的大盘组件，这里用 SVG 画：
 * 单色（线用正文色、面积用正文色的淡填充、网格用细分割线），不引图表库。
 *
 * SVG 按容器宽度拉伸（preserveAspectRatio="none"），只放线和面积，线宽不随拉伸变（non-scaling-stroke）；
 * 刻度文字、悬停的点和数值用 HTML 叠在上面，不然会被拉扁。没有数据的那天传 null，线在那里断开。
 */
import { useId, useState, type MouseEvent } from 'react';

export interface TrendPoint {
  /** 横轴标签，一般是日期（YYYY-MM-DD） */
  readonly label: string;
  readonly value: number | null;
}

interface Props {
  /** 图的标题，显示在图上方，也是读屏念的名字，比如「每天的提问数」 */
  readonly title: string;
  readonly points: readonly TrendPoint[];
  /** 数值怎么显示（悬停与纵轴最大值），默认千分位整数 */
  readonly format?: (value: number) => string;
  /** 纵轴上限；不给按数据最大值取整（比例类传 1） */
  readonly max?: number;
  readonly height?: number;
}

const WIDTH = 1000; // viewBox 宽，实际按容器拉伸
const defaultFormat = (value: number) => value.toLocaleString('zh-CN');

/** 纵轴上限取「好看」的整数：1、2、5 乘 10 的幂，至少为 1。 */
export function niceMax(value: number): number {
  if (value <= 1) return 1;
  const base = 10 ** Math.floor(Math.log10(value));
  const step = [1, 2, 5, 10].find((m) => m * base >= value) ?? 10;
  return step * base;
}

/** 连续有值的几段：null 处断开，每段各画一条线、一块面积。 */
function segments(points: readonly TrendPoint[]): number[][] {
  const runs: number[][] = [];
  points.forEach((point, index) => {
    if (point.value === null) return;
    const last = runs.at(-1);
    if (last && last.at(-1) === index - 1) last.push(index);
    else runs.push([index]);
  });
  return runs;
}

export function TrendChart({ title, points, format = defaultFormat, max, height = 160 }: Props) {
  const [hover, setHover] = useState<number | null>(null);
  const gradient = useId();
  const values = points.map((p) => p.value).filter((v): v is number => v !== null);
  const top = max && max > 0 ? max : niceMax(Math.max(0, ...values));
  const step = points.length > 1 ? WIDTH / (points.length - 1) : 0;
  const x = (index: number) => (points.length > 1 ? index * step : WIDTH / 2);
  const y = (value: number) => height - (Math.min(value, top) / top) * height;
  const runs = segments(points);
  const line = (run: number[]) =>
    run.map((i, n) => `${n === 0 ? 'M' : 'L'}${x(i)},${y(points[i]?.value ?? 0)}`).join(' ');
  const area = (run: number[]) =>
    `${line(run)} L${x(run.at(-1) ?? 0)},${height} L${x(run[0] ?? 0)},${height} Z`;

  const onMove = (event: MouseEvent<HTMLDivElement>) => {
    const box = event.currentTarget.getBoundingClientRect();
    if (box.width === 0 || points.length === 0) return;
    const ratio = Math.min(1, Math.max(0, (event.clientX - box.left) / box.width));
    setHover(Math.round(ratio * (points.length - 1)));
  };
  const hovered = hover === null ? undefined : points[hover];
  const left = (index: number) => `${(x(index) / WIDTH) * 100}%`;
  const ticks =
    points.length > 2 ? [0, Math.floor((points.length - 1) / 2), points.length - 1] : points.map((_, i) => i);
  const latest = [...points].reverse().find((p) => p.value !== null);

  return (
    <figure className="kw-trend" aria-label={title}>
      <figcaption className="kw-trend-title">{title}</figcaption>
      <div className="kw-trend-axis">{format(top)}</div>
      <div
        className="kw-trend-plot"
        style={{ height }}
        onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}
      >
        <svg
          viewBox={`0 0 ${WIDTH} ${height}`}
          preserveAspectRatio="none"
          role="img"
          aria-label={
            latest
              ? `${title}，最近一天 ${latest.label} 为 ${format(latest.value ?? 0)}`
              : `${title}，暂无数据`
          }
        >
          <defs>
            <linearGradient id={gradient} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" className="kw-trend-fill-top" />
              <stop offset="100%" className="kw-trend-fill-bottom" />
            </linearGradient>
          </defs>
          {[0, 0.5, 1].map((r) => (
            <line
              key={r}
              className="kw-trend-grid"
              x1={0}
              x2={WIDTH}
              y1={height * r}
              y2={height * r}
              vectorEffect="non-scaling-stroke"
            />
          ))}
          {runs.map((run) => (
            <g key={run[0]}>
              <path d={area(run)} fill={`url(#${gradient})`} />
              <path d={line(run)} className="kw-trend-line" vectorEffect="non-scaling-stroke" />
            </g>
          ))}
        </svg>
        {hover !== null && hovered ? (
          <>
            <span className="kw-trend-guide" style={{ left: left(hover) }} aria-hidden="true" />
            {hovered.value !== null ? (
              <span
                className="kw-trend-dot"
                style={{ left: left(hover), top: y(hovered.value) }}
                aria-hidden="true"
              />
            ) : null}
            <span
              className="kw-trend-tip"
              style={{ left: left(hover) }}
              data-side={hover > points.length / 2 ? 'left' : 'right'}
              role="status"
            >
              {hovered.label}：{hovered.value === null ? '无数据' : format(hovered.value)}
            </span>
          </>
        ) : null}
      </div>
      <div className="kw-trend-ticks" aria-hidden="true">
        {points.length > 0 ? ticks.map((index) => <span key={index}>{points[index]?.label}</span>) : null}
      </div>
    </figure>
  );
}
