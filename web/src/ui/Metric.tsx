/** 大盘上的一个指标：名称、大号数值、一句补充（和上一段时间比、口径说明）。几个并排放在 MetricGrid 里。 */
import type { ReactNode } from 'react';

interface Props {
  readonly label: string;
  readonly value: ReactNode;
  readonly hint?: ReactNode;
}

export function Metric({ label, value, hint }: Props) {
  return (
    <div className="kw-metric" role="group" aria-label={label}>
      <div className="kw-metric-label">{label}</div>
      <div className="kw-metric-value">{value}</div>
      {hint ? <div className="kw-metric-hint">{hint}</div> : null}
    </div>
  );
}

export function MetricGrid({ children }: { readonly children: ReactNode }) {
  return <div className="kw-metric-grid">{children}</div>;
}
