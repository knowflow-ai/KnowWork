/**
 * 输入框里还没发出去的附件能不能直接进编辑器：来源给得出建副本、又说这里开着在线编辑（sources.ts 的 editsDrafts）。
 * 新对话页和会话页都用：新对话页点了要先建对话，开不了时卡片就不给点，免得白建一个。问一次就记住（编辑器配置要重启服务才变）。
 */
import { useQuery } from '@tanstack/react-query';
import { useWorkspaceStore } from '../../../stores/workspace';
import type { ConversationFiles } from '../sources';

export function useDraftsEditable(files: ConversationFiles | undefined): boolean {
  const ask = files?.workingCopy ? files.editsDrafts : undefined;
  // 按空间记：在线编辑按空间的权限问，换了空间要重新问
  const workspace = useWorkspaceStore((state) => state.id);
  const answer = useQuery({
    queryKey: ['chat', 'drafts-editable', workspace],
    queryFn: () => (ask ? ask() : Promise.resolve(false)),
    enabled: ask !== undefined,
    staleTime: Infinity,
  });
  return ask !== undefined && answer.data === true;
}
