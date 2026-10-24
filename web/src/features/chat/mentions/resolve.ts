/**
 * 重新回答时把那一问的 @ 补全：事件流里的目录只有路径（宿主那边不记目录下有哪些库），
 * 按现在的知识库目录重新展开；展开不出来的目录（改名、删空了）就去掉。
 */
import type { QueryClient } from '@tanstack/react-query';
import { getMentionOptions, type Mention } from '../../../api/mentions';
import { expandFolder } from './candidates';
import { MENTION_OPTIONS_KEY } from './useMentionMenu';

export async function resolveMentions(
  client: QueryClient,
  mentions: readonly Mention[],
): Promise<readonly Mention[]> {
  if (!mentions.some((m) => m.type === 'folder' && !m.bases?.length)) return mentions;
  const options = await client.fetchQuery({
    queryKey: MENTION_OPTIONS_KEY,
    queryFn: getMentionOptions,
    staleTime: 60_000,
  });
  return mentions.flatMap((mention) => {
    if (mention.type !== 'folder' || mention.bases?.length) return [mention];
    const bases = expandFolder(mention.id, options.bases);
    return bases.length > 0 ? [{ ...mention, bases }] : [];
  });
}
