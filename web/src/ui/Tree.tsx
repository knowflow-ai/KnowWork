/**
 * 可展开的树（部门树、树形选择框）：默认全部展开（defaultCollapsed 给的节点开始是收起的），点三角收起，点名称选中（分组节点点名称是展开收起）；
 * 选中项浅灰底、正文色（界面规范第 1 节「列表选中项」）。多选时（multiple）选中的几项都是浅灰底，行尾再打一个勾，
 * 和组件库多选下拉的样子一致；被选中项包含了的（covered，例如选了目录，它下面的知识库）行尾写「已包含」、点不了。
 * 组件库没有树，键盘与读屏照 WAI-ARIA 的 tree 模式。
 * 单选时选中项从外面换了（例如在详情里点了一个字段），把它的上级都展开、滚到看得见的地方，不让人迷路。
 */
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Check, ChevronDown, ChevronRight } from '@openai/apps-sdk-ui/components/Icon';
import { cx } from './cx';

export interface TreeNode {
  readonly key: string;
  readonly title: ReactNode;
  readonly children: readonly TreeNode[];
  /** false 时只是分组（例如树形选择框里的知识源），点名称展开收起，不能选中 */
  readonly selectable?: boolean;
}

interface Props {
  readonly nodes: readonly TreeNode[];
  /** 单选时是一个 key，多选时是 key 的列表 */
  readonly selected?: string | readonly string[];
  /** 多选：选中项行尾打勾，读屏报 aria-multiselectable */
  readonly multiple?: boolean;
  /** 多选时被选中项包含了的节点：不能再单独选 */
  readonly covered?: ReadonlySet<string>;
  readonly onSelect: (key: string) => void;
  /** 开始时收起的节点（例如模型树里的每张表，展开才看字段）；只在第一次渲染时用，要重来就换 key */
  readonly defaultCollapsed?: readonly string[];
  readonly 'aria-label': string;
  readonly className?: string;
}

interface BranchProps {
  readonly node: TreeNode;
  readonly depth: number;
  readonly isSelected: (key: string) => boolean;
  readonly isCovered: (key: string) => boolean;
  readonly multiple: boolean;
  readonly collapsed: ReadonlySet<string>;
  readonly onToggle: (key: string) => void;
  readonly onSelect: (key: string) => void;
}

function Branch({
  node,
  depth,
  isSelected,
  isCovered,
  multiple,
  collapsed,
  onToggle,
  onSelect,
}: BranchProps) {
  const hasChildren = node.children.length > 0;
  const open = hasChildren && !collapsed.has(node.key);
  const covered = isCovered(node.key);
  const chosen = isSelected(node.key);
  const selected = chosen || covered;
  const row = useRef<HTMLDivElement>(null);
  useEffect(() => {
    // jsdom 里没有 scrollIntoView
    if (chosen) row.current?.scrollIntoView?.({ block: 'nearest' });
  }, [chosen]);
  return (
    <li
      role="treeitem"
      aria-selected={selected}
      aria-expanded={hasChildren ? open : undefined}
      className="kw-tree-item"
    >
      <div
        ref={row}
        className={cx('kw-tree-row', chosen && 'is-selected')}
        style={{ paddingLeft: depth * 16 + 4 }}
      >
        {hasChildren ? (
          <button
            type="button"
            className="kw-tree-toggle"
            aria-label={open ? '收起' : '展开'}
            onClick={() => onToggle(node.key)}
          >
            {open ? <ChevronDown /> : <ChevronRight />}
          </button>
        ) : (
          <span className="kw-tree-toggle" aria-hidden="true" />
        )}
        <button
          type="button"
          className={cx('kw-tree-title', node.selectable === false && 'is-group', covered && 'is-covered')}
          disabled={covered}
          onClick={() => (node.selectable === false ? onToggle(node.key) : onSelect(node.key))}
        >
          {node.title}
        </button>
        {covered ? (
          <span className="kw-tree-covered">已包含</span>
        ) : multiple && chosen ? (
          <Check className="kw-tree-check" aria-hidden />
        ) : null}
      </div>
      {open ? (
        <ul role="group" className="kw-tree-children">
          {node.children.map((child) => (
            <Branch
              key={child.key}
              node={child}
              depth={depth + 1}
              isSelected={isSelected}
              isCovered={isCovered}
              multiple={multiple}
              collapsed={collapsed}
              onToggle={onToggle}
              onSelect={onSelect}
            />
          ))}
        </ul>
      ) : null}
    </li>
  );
}

/** 从根到这个节点的上级（不含它自己）；找不到是 null */
function ancestorsOf(nodes: readonly TreeNode[], key: string): readonly string[] | null {
  for (const node of nodes) {
    if (node.key === key) return [];
    const below = ancestorsOf(node.children, key);
    if (below) return [node.key, ...below];
  }
  return null;
}

export function Tree({
  nodes,
  selected,
  multiple = false,
  covered,
  onSelect,
  defaultCollapsed,
  className,
  ...rest
}: Props) {
  // 一开始就选中了深处的一项（例如带着地址进来）：它的上级不收起
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(() => {
    const path = typeof selected === 'string' ? (ancestorsOf(nodes, selected) ?? []) : [];
    return new Set((defaultCollapsed ?? []).filter((key) => !path.includes(key)));
  });
  // 选中项换了：把它的上级展开（渲染时按上一次的选中项比较，React 推荐的「随属性调整状态」写法）
  const [revealed, setRevealed] = useState(selected);
  if (typeof selected === 'string' && selected !== revealed) {
    setRevealed(selected);
    const path = ancestorsOf(nodes, selected) ?? [];
    if (path.some((key) => collapsed.has(key)))
      setCollapsed(new Set([...collapsed].filter((key) => !path.includes(key))));
  }
  const isCovered = (key: string) => covered?.has(key) ?? false;
  const isSelected = (key: string) =>
    typeof selected === 'string' ? selected === key : (selected?.includes(key) ?? false);
  const toggle = (key: string) =>
    setCollapsed((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  return (
    <ul
      role="tree"
      aria-label={rest['aria-label']}
      aria-multiselectable={multiple || undefined}
      className={cx('kw-tree', className)}
    >
      {nodes.map((node) => (
        <Branch
          key={node.key}
          node={node}
          depth={0}
          isSelected={isSelected}
          isCovered={isCovered}
          multiple={multiple}
          collapsed={collapsed}
          onToggle={toggle}
          onSelect={onSelect}
        />
      ))}
    </ul>
  );
}
