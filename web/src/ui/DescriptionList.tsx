/** 详情里的「名称：值」列表。不加边框，标签说明色、值正文色，照 ChatGPT 设置页的一行一项。 */
import type { ReactNode } from 'react';
import { cx } from './cx';

export interface DescriptionItem {
  readonly label: ReactNode;
  readonly value: ReactNode;
  readonly key?: string;
}

export function DescriptionList({
  items,
  className,
}: {
  readonly items: readonly DescriptionItem[];
  readonly className?: string;
}) {
  return (
    <dl className={cx('kw-descriptions', className)}>
      {items.map((item, i) => (
        <div key={item.key ?? i} className="kw-descriptions-row">
          <dt>{item.label}</dt>
          <dd>{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}
