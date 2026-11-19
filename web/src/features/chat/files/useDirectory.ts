/** 文件面板的一层：列出来并在显示期间跟着它的变化（来源的 list、watch），缓存键按对话分，「重新读取」按前缀一起刷新 */
import { useEffect } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import type { ConversationFiles } from '../sources';

type List = NonNullable<ConversationFiles['list']>;

/** 一层的缓存键：面板的「重新读取」按对话前缀一起刷新 */
export function filesKey(conversationId: string, path?: string): readonly unknown[] {
  return path === undefined
    ? ['conversations', conversationId, 'files']
    : ['conversations', conversationId, 'files', path];
}

/** 列一层，并在它显示期间跟着它的变化 */
export function useDirectory(
  conversationId: string,
  path: string,
  list: List,
  watch: ConversationFiles['watch'],
) {
  const client = useQueryClient();
  const query = useQuery({
    queryKey: filesKey(conversationId, path),
    queryFn: () => list(conversationId, path),
    // 收起再展开、面板关了再开都重新列（官方：再次打开时重新列举），列的时候先显示上次的
    refetchOnMount: 'always',
    staleTime: Infinity,
  });
  useEffect(
    () =>
      watch?.(conversationId, path, () => {
        void client.invalidateQueries({ queryKey: filesKey(conversationId, path), exact: true });
      }),
    [client, conversationId, path, watch],
  );
  return query;
}
