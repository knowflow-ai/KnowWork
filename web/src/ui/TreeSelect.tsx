/**
 * 树形选择框：外观同组件库的 Select（直接用它的 SelectControl），点开是带搜索的可展开树（Tree）。
 * 用在要按层级挑的地方，例如授权时选知识源下的目录或知识库：平铺的下拉看不出层级，同名目录也分不清。
 * 浮层放进 portal（Radix Popover，对话框的滚动区裁不掉）；打开时焦点交给搜索框（照 Combobox 自己管，
 * 组件库 Popover 的自动聚焦放在对话框里会被焦点陷阱拉回）。分组节点（selectable: false）只用来展开收起。
 */
import { useRef, useState, type ReactNode } from 'react';
import { Popover } from 'radix-ui';
import { Input } from '@openai/apps-sdk-ui/components/Input';
import { Search } from '@openai/apps-sdk-ui/components/Icon';
import { SelectControl } from '@openai/apps-sdk-ui/components/SelectControl';
import { Tree, type TreeNode } from './Tree';

export interface TreeSelectNode {
  readonly key: string;
  readonly label: string;
  readonly children: readonly TreeSelectNode[];
  /** false 时只是分组，不能选 */
  readonly selectable?: boolean;
  /** 名字前面的类型图标（目录、知识库……），只在树里显示；搜索与选中后的路径只用 label */
  readonly icon?: ReactNode;
}

interface Props {
  readonly nodes: readonly TreeSelectNode[];
  readonly value: string;
  readonly onChange: (key: string) => void;
  readonly placeholder: string;
  readonly searchPlaceholder: string;
  readonly emptyMessage: string;
  readonly disabled?: boolean;
  readonly invalid?: boolean;
  /** 树的名字（读屏、测试按它找） */
  readonly 'aria-label': string;
}

/** 只留名字里有 query 的节点和它们的上级；节点本身匹配时整棵子树都留（搜目录名时连同下面的一起看到）。 */
export function filterTree(nodes: readonly TreeSelectNode[], query: string): readonly TreeSelectNode[] {
  const wanted = query.trim().toLowerCase();
  if (!wanted) return nodes;
  return nodes.flatMap((node) => {
    if (node.label.toLowerCase().includes(wanted)) return [node];
    const children = filterTree(node.children, wanted);
    return children.length > 0 ? [{ ...node, children }] : [];
  });
}

/** 节点从根起的完整路径（「扩展知识库 / 产品资料 / 电风扇说明书」）；找不到返回 undefined。 */
export function pathOf(nodes: readonly TreeSelectNode[], key: string): string | undefined {
  for (const node of nodes) {
    if (node.key === key) return node.label;
    const below = pathOf(node.children, key);
    if (below !== undefined) return `${node.label} / ${below}`;
  }
  return undefined;
}

function toTree(nodes: readonly TreeSelectNode[]): readonly TreeNode[] {
  return nodes.map((node) => ({
    key: node.key,
    title: node.icon ? (
      <span className="kw-tree-node">
        <span className="kw-tree-node-icon" aria-hidden>
          {node.icon}
        </span>
        {node.label}
      </span>
    ) : (
      node.label
    ),
    selectable: node.selectable,
    children: toTree(node.children),
  }));
}

export function TreeSelect({
  nodes,
  value,
  onChange,
  placeholder,
  searchPlaceholder,
  emptyMessage,
  disabled,
  invalid,
  ...rest
}: Props) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const search = useRef<HTMLInputElement>(null);
  const shown = filterTree(nodes, query);
  const selected = value ? pathOf(nodes, value) : undefined;
  const toggle = (next: boolean) => {
    setOpen(next);
    if (!next) setQuery('');
  };
  return (
    <Popover.Root open={open} onOpenChange={toggle}>
      <Popover.Anchor asChild>
        <SelectControl
          block
          selected={selected !== undefined}
          disabled={disabled}
          invalid={invalid}
          aria-haspopup="tree"
          aria-expanded={open}
          onInteract={() => toggle(!open)}
        >
          {selected ?? placeholder}
        </SelectControl>
      </Popover.Anchor>
      <Popover.Portal>
        <Popover.Content
          className="kw-combobox-menu kw-tree-select-menu"
          align="start"
          sideOffset={4}
          style={{ width: 'var(--radix-popover-trigger-width)' }}
          onOpenAutoFocus={(event) => {
            event.preventDefault();
            search.current?.focus();
          }}
        >
          <Input
            ref={search}
            size="sm"
            aria-label={searchPlaceholder}
            placeholder={searchPlaceholder}
            startAdornment={<Search className="fill-secondary" width={14} height={14} />}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          {shown.length === 0 ? (
            <div className="kw-combobox-empty">{emptyMessage}</div>
          ) : (
            <Tree
              // 搜索时换一棵树：之前收起过的节点全部重新展开，匹配的都看得到
              key={query.trim() ? 'search' : 'all'}
              aria-label={rest['aria-label']}
              className="kw-tree-select-tree"
              nodes={toTree(shown)}
              selected={value}
              onSelect={(key) => {
                onChange(key);
                toggle(false);
              }}
            />
          )}
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
