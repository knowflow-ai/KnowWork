/**
 * 授权时选知识库：按知识源分组列出，每组第一项是「全部知识库」，选它就是授 <知识源id>.*
 * （这个来源下的全部，含以后新开放的）。可按名称搜索。
 */
import { Select } from '@openai/apps-sdk-ui/components/Select';
import type { SourceBases } from '../../../api/knowledge';

function sourceTitle(s: SourceBases): string {
  const notes = [s.enabled ? '' : '已停用', s.error ? `知识库列表取不到：${s.error}` : ''].filter(Boolean);
  return notes.length > 0 ? `${s.sourceName}（${notes.join('；')}）` : s.sourceName;
}

function groupsOf(sources: readonly SourceBases[]) {
  return sources.map((s) => ({
    label: sourceTitle(s),
    options: [
      { value: `${s.sourceId}.*`, label: `${s.sourceName} · 全部知识库` },
      ...s.bases.map((b) => ({ value: b.id, label: `${s.sourceName} · ${b.name}` })),
    ],
  }));
}

export function KnowledgeBasePicker({
  sources,
  value,
  disabled,
  onChange,
}: {
  readonly sources: readonly SourceBases[];
  readonly value: string;
  readonly disabled: boolean;
  readonly onChange: (id: string) => void;
}) {
  return (
    <Select
      block
      options={groupsOf(sources)}
      value={value}
      disabled={disabled}
      placeholder={sources.length > 0 ? '选择知识源或其中的知识库' : '还没有知识源'}
      searchPlaceholder="搜索知识库"
      searchEmptyMessage="没有匹配的选项"
      onChange={(option) => onChange(option.value)}
    />
  );
}
