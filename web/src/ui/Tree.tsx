/**
 * 可展开的树（部门树、树形选择框）：默认全部展开，点三角收起，点名称选中（分组节点点名称是展开收起）；
 * 选中项浅灰底、正文色（界面规范第 1 节「列表选中项」）。多选时（multiple）选中的几项都是浅灰底，行尾再打一个勾，
 * 和组件库多选下拉的样子一致。
 * 组件库没有树，键盘与读屏照 WAI-ARIA 的 tree 模式。
 */
import { useState, type ReactNode } from 'react';
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
  readonly onSelect: (key: string) => void;
  readonly 'aria-label': string;
  readonly className?: string;
}

interface BranchProps {
  readonly node: TreeNode;
  readonly depth: number;
  readonly isSelected: (key: string) => boolean;
  readonly multiple: boolean;
  readonly collapsed: ReadonlySet<string>;
  readonly onToggle: (key: string) => void;
  readonly onSelect: (key: string) => void;
}

function Branch({ node, depth, isSelected, multiple, collapsed, onToggle, onSelect }: BranchProps) {
  const hasChildren = node.children.length > 0;
  const open = hasChildren && !collapsed.has(node.key);
  const selected = isSelected(node.key);
  return (
    <li
      role="treeitem"
      aria-selected={selected}
      aria-expanded={hasChildren ? open : undefined}
      className="kw-tree-item"
    >
      <div className={cx('kw-tree-row', selected && 'is-selected')} style={{ paddingLeft: depth * 16 + 4 }}>
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
          className={cx('kw-tree-title', node.selectable === false && 'is-group')}
          onClick={() => (node.selectable === false ? onToggle(node.key) : onSelect(node.key))}
        >
          {node.title}
        </button>
        {multiple && selected ? <Check className="kw-tree-check" aria-hidden /> : null}
      </div>
      {open ? (
        <ul role="group" className="kw-tree-children">
          {node.children.map((child) => (
            <Branch
              key={child.key}
              node={child}
              depth={depth + 1}
              isSelected={isSelected}
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

export function Tree({ nodes, selected, multiple = false, onSelect, className, ...rest }: Props) {
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(new Set());
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
          multiple={multiple}
          collapsed={collapsed}
          onToggle={toggle}
          onSelect={onSelect}
        />
      ))}
    </ul>
  );
}
