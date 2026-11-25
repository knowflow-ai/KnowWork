/**
 * 打开的文档的标签条（照 DSH 官方右侧栏的标签条：@deepseek-ai/dsh-client-ui-dockkit 0.2.3-alpha.2 的 TabPanel）。
 * 组件库没有带关闭的文档标签，自建：
 * - 每个标签是一枚胶囊：类型图标加文件名，名字太长在右端渐隐；当前标签填底色，别的只有字，相邻标签之间一条细竖线
 *   （当前标签两边的不画）；
 * - 关闭按钮叠在标签右端，当前标签、悬停或键盘焦点在它上面时才出现；中键点标签任何地方都是关闭（不选中它）；
 * - 右键：关闭、关闭其他、关闭右侧；
 * - 键盘照 WAI-ARIA 标签页：左右键（Home、End）在标签间移焦点，Enter、空格选中，Delete 关掉焦点所在的标签；
 * - 标签多了在条里横向滚动，当前标签总滚到看得见的地方；end 放在条的右端（切换类控件、文件的操作），不跟着滚。
 * 开在哪、关掉以后选哪个由 docTabList.ts 照官方的规则算；关掉最后一个怎么办由调用方定。
 */
import { useEffect, useRef, type KeyboardEvent, type ReactNode } from 'react';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { X } from '@openai/apps-sdk-ui/components/Icon';
import { ContextMenu } from './ContextMenu';
import { cx } from './cx';

export interface DocTab {
  readonly id: string;
  readonly title: string;
  readonly icon?: ReactNode;
  /** 悬停时显示的完整说明（例如文件路径）；不给用标题 */
  readonly hint?: string;
}

export function DocTabs({
  tabs,
  activeId,
  onSelect,
  onClose,
  label,
  end,
}: {
  readonly tabs: readonly DocTab[];
  readonly activeId: string | undefined;
  readonly onSelect: (id: string) => void;
  /** 关掉这几个标签（一个，或右键的「关闭其他」「关闭右侧」） */
  readonly onClose: (ids: readonly string[]) => void;
  readonly label: string;
  readonly end?: ReactNode;
}) {
  const chips = useRef(new Map<string, HTMLElement>());
  // 当前标签换了（新开的、关掉别的以后轮到的）：滚到看得见的地方
  useEffect(() => {
    const chip = activeId === undefined ? undefined : chips.current.get(activeId);
    chip?.scrollIntoView?.({ block: 'nearest', inline: 'nearest' });
  }, [activeId, tabs.length]);

  const moveFocus = (event: KeyboardEvent, index: number) => {
    const target = focusTarget(event.key, index, tabs.length);
    if (target === undefined) return false;
    event.preventDefault();
    chips.current.get(tabs[target]!.id)?.focus();
    return true;
  };

  return (
    <div className="kw-doc-tabs">
      <div className="kw-doc-tabs-strip" role="tablist" aria-label={label}>
        {tabs.map((tab, index) => {
          const active = tab.id === activeId;
          const others = tabs.filter((other) => other.id !== tab.id).map((other) => other.id);
          const right = tabs.slice(index + 1).map((other) => other.id);
          return (
            <ContextMenu
              key={tab.id}
              items={[
                { label: '关闭', onSelect: () => onClose([tab.id]) },
                { label: '关闭其他', onSelect: () => onClose(others), disabled: others.length === 0 },
                { label: '关闭右侧', onSelect: () => onClose(right), disabled: right.length === 0 },
              ]}
            >
              <div
                ref={(element) => {
                  if (element === null) chips.current.delete(tab.id);
                  else chips.current.set(tab.id, element);
                }}
                role="tab"
                aria-selected={active}
                tabIndex={active ? 0 : -1}
                title={tab.hint ?? tab.title}
                className={cx('kw-doc-tab', active && 'is-active')}
                onClick={() => onSelect(tab.id)}
                onMouseDown={(event) => {
                  // 中键：别触发浏览器的自动滚动
                  if (event.button === 1) event.preventDefault();
                }}
                onAuxClick={(event) => {
                  if (event.button !== 1) return;
                  event.preventDefault();
                  onClose([tab.id]);
                }}
                onKeyDown={(event) => {
                  if (moveFocus(event, index)) return;
                  if (event.target !== event.currentTarget) return;
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    onSelect(tab.id);
                  } else if (event.key === 'Delete') {
                    event.preventDefault();
                    onClose([tab.id]);
                  }
                }}
              >
                <span className="kw-doc-tab-title">
                  {tab.icon}
                  <span className="kw-doc-tab-name">{tab.title}</span>
                </span>
                <span className="kw-doc-tab-close">
                  <Button
                    color="secondary"
                    variant="ghost"
                    size="3xs"
                    uniform
                    tabIndex={-1}
                    aria-label={`关闭 ${tab.title}`}
                    onClick={(event) => {
                      event.stopPropagation();
                      onClose([tab.id]);
                    }}
                  >
                    <X />
                  </Button>
                </span>
              </div>
            </ContextMenu>
          );
        })}
      </div>
      {end ? <div className="kw-doc-tabs-end">{end}</div> : null}
    </div>
  );
}

/** 键盘移焦点到哪个标签：左右键前后一个（到头转回来），Home、End 首尾；别的键不管 */
function focusTarget(key: string, index: number, count: number): number | undefined {
  if (key === 'ArrowRight') return (index + 1) % count;
  if (key === 'ArrowLeft') return (index - 1 + count) % count;
  if (key === 'Home') return 0;
  if (key === 'End') return count - 1;
  return undefined;
}
