/**
 * 右键菜单：组件库只有点按钮弹出的 Menu，没有在鼠标处弹出的；用 Radix ContextMenu 自建，样子用组件库菜单的那套变量
 * （--menu-gutter、--menu-radius、--menu-item-padding……），和组件库的 Menu 看起来一样。Shift+F10、菜单键也能打开（Radix 自带）。
 * 没有菜单项时不包，右键照浏览器原样。
 */
import type { ReactElement } from 'react';
import { ContextMenu as Radix } from 'radix-ui';

export interface ContextMenuItem {
  readonly label: string;
  readonly onSelect: () => void;
  readonly disabled?: boolean;
}

export function ContextMenu({
  items,
  children,
}: {
  readonly items: readonly ContextMenuItem[];
  readonly children: ReactElement;
}) {
  if (items.length === 0) return children;
  return (
    <Radix.Root>
      <Radix.Trigger asChild>{children}</Radix.Trigger>
      <Radix.Portal>
        <Radix.Content className="kw-context-menu" collisionPadding={8}>
          {items.map((item) => (
            <Radix.Item
              key={item.label}
              className="kw-context-menu-item"
              disabled={item.disabled}
              onSelect={item.onSelect}
            >
              {item.label}
            </Radix.Item>
          ))}
        </Radix.Content>
      </Radix.Portal>
    </Radix.Root>
  );
}
