/**
 * 页内标签页：Radix Tabs。组件库没有标签页，照 ChatGPT 设置页的写法：文字标签、选中项正文色加一道下划线，
 * 未选中是说明色。两三个互斥视图的切换用分段控件（SegmentedControl），不用这个。
 */
import type { ReactNode } from 'react';
import { Tabs as Radix } from 'radix-ui';
import { cx } from './cx';

export interface TabItem {
  readonly key: string;
  readonly label: ReactNode;
  readonly children: ReactNode;
}

interface Props {
  readonly items: readonly TabItem[];
  readonly value?: string;
  readonly defaultValue?: string;
  readonly onChange?: (key: string) => void;
  /** 标签栏右侧的附加内容（例如这一页的主操作）。 */
  readonly extra?: ReactNode;
  readonly className?: string;
}

export function Tabs({ items, value, defaultValue, onChange, extra, className }: Props) {
  return (
    <Radix.Root
      className={cx('kw-tabs', className)}
      value={value}
      defaultValue={defaultValue ?? items[0]?.key}
      onValueChange={onChange}
    >
      <div className="kw-tabs-bar">
        <Radix.List className="kw-tabs-list">
          {items.map((item) => (
            <Radix.Trigger key={item.key} value={item.key} className="kw-tabs-trigger">
              {item.label}
            </Radix.Trigger>
          ))}
        </Radix.List>
        {extra ? <div className="kw-tabs-extra">{extra}</div> : null}
      </div>
      {items.map((item) => (
        <Radix.Content key={item.key} value={item.key} className="kw-tabs-panel">
          {item.children}
        </Radix.Content>
      ))}
    </Radix.Root>
  );
}
