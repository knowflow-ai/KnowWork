/**
 * 授权时选知识库：树形选择，按知识源分组（分组本身不能选），每组第一项是「全部知识库」，选它就是授 <知识源id>.*
 * （这个来源下的全部，含以后新开放的）。扩展给了层级树的知识源（插槽 grants.knowledge，例如目录 → 知识库）按树显示，
 * 选目录就是按目录授权，下面的子目录和知识库都算；其余知识源平铺知识库。值是完整资源（`类型:id`）。
 * 给专家选能查的知识库时也用它（folders 为假：目录只能展开、不能选，专家的范围只认知识库和「全部知识库」）。
 */
import { BookClosed, BookOpen } from '@openai/apps-sdk-ui/components/Icon';
import type { SourceBases } from '../../../api/knowledge';
import type { GrantTreeNode } from '../../../slots/registry';
import { TreeSelect, type TreeSelectNode } from '../../../ui/TreeSelect';
import { resourceIcon } from './icons';

function sourceTitle(s: SourceBases): string {
  const notes = [s.enabled ? '' : '已停用', s.error ? `知识库列表取不到：${s.error}` : ''].filter(Boolean);
  return notes.length > 0 ? `${s.sourceName}（${notes.join('；')}）` : s.sourceName;
}

const FOLDER = 'knowledge_folder:';

function fromTree(nodes: readonly GrantTreeNode[], folders: boolean): readonly TreeSelectNode[] {
  return nodes.map((node) => ({
    key: node.resource,
    label: node.name,
    icon: resourceIcon(node.resource),
    ...(node.selectable === false || (!folders && node.resource.startsWith(FOLDER))
      ? { selectable: false }
      : {}),
    children: fromTree(node.children, folders),
  }));
}

export function knowledgeNodes(
  sources: readonly SourceBases[],
  trees: Readonly<Record<string, readonly GrantTreeNode[]>>,
  folders = true,
): readonly TreeSelectNode[] {
  return sources.map((s) => {
    const tree = trees[s.sourceId];
    const below = tree
      ? fromTree(tree, folders)
      : s.bases.map((b) => ({
          key: `knowledge_base:${b.id}`,
          label: b.name,
          icon: <BookClosed />,
          children: [],
        }));
    const all = `knowledge_base:${s.sourceId}.*`;
    return {
      key: `source:${s.sourceId}`,
      label: sourceTitle(s),
      // 知识源和管理后台「知识源」菜单同一个图标
      icon: <BookOpen />,
      selectable: false,
      children: [
        { key: all, label: '全部知识库', icon: resourceIcon(all), coversSiblings: true, children: [] },
        ...below,
      ],
    };
  });
}

export function KnowledgeBasePicker({
  sources,
  trees,
  value,
  disabled,
  invalid,
  folders = true,
  placeholder,
  onChange,
}: {
  readonly sources: readonly SourceBases[];
  readonly trees: Readonly<Record<string, readonly GrantTreeNode[]>>;
  readonly value: readonly string[];
  readonly disabled?: boolean;
  readonly invalid?: boolean;
  /** 目录能不能选（按目录授权）；不能选时只用来展开 */
  readonly folders?: boolean;
  /** 一个都没选时写的；不给就是「选择知识库」 */
  readonly placeholder?: string;
  readonly onChange: (resources: string[]) => void;
}) {
  return (
    <TreeSelect
      multiple
      aria-label="知识库"
      nodes={knowledgeNodes(sources, trees, folders)}
      value={value}
      disabled={disabled}
      invalid={invalid}
      placeholder={sources.length > 0 ? (placeholder ?? '选择知识库') : '还没有知识源'}
      searchPlaceholder="搜索知识库或目录"
      emptyMessage="没有匹配的选项"
      onChange={onChange}
    />
  );
}
