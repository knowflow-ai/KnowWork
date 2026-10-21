/** 会话列表（分页）与改名、删除。 */
import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  deleteConversation,
  listConversations,
  renameConversation,
  type ConversationSummary,
} from '../../api/conversations';

export const CONVERSATIONS_KEY = ['conversations'] as const;

export function useConversationList() {
  return useInfiniteQuery({
    queryKey: CONVERSATIONS_KEY,
    queryFn: ({ pageParam }) => listConversations(pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next ?? undefined,
  });
}

/** 拍平各页并按 id 去重（翻页期间有会话更新时，同一会话可能出现在两页）。 */
export function flattenPages(
  pages: readonly { readonly items: readonly ConversationSummary[] }[] | undefined,
) {
  const seen = new Set<string>();
  return (pages ?? []).flatMap((page) =>
    page.items.filter((item) => {
      if (seen.has(item.id)) return false;
      seen.add(item.id);
      return true;
    }),
  );
}

export function useRenameConversation() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, title }: { readonly id: string; readonly title: string }) =>
      renameConversation(id, title),
    onSuccess: () => client.invalidateQueries({ queryKey: CONVERSATIONS_KEY }),
  });
}

export function useDeleteConversation() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteConversation(id),
    onSuccess: () => client.invalidateQueries({ queryKey: CONVERSATIONS_KEY }),
  });
}
