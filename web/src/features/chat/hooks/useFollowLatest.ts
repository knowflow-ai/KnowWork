/**
 * 消息区跟不跟着最新的（照 ChatGPT、DeepSeek）：停在底部附近时，新内容来了自动滚到底；往上翻了就不再自动滚
 * （边出字边看前面的不会被拽走），输入框上方浮一个「回到最新」，点了平滑滚到底。自己发消息时总是回到底部
 * （MessageList 判断）。
 */
import { useCallback, useRef, useState } from 'react';

/** 离底部不超过这么多像素算「在最新处」：留一点余量，滚到差一点点也算 */
export const LATEST_GAP = 80;

export function useFollowLatest() {
  const ref = useRef<HTMLDivElement>(null);
  const [atLatest, setAtLatest] = useState(true);
  const onScroll = useCallback(() => {
    const element = ref.current;
    if (!element) return;
    setAtLatest(element.scrollHeight - element.scrollTop - element.clientHeight <= LATEST_GAP);
  }, []);
  const toLatest = useCallback(() => {
    const element = ref.current;
    element?.scrollTo({ top: element.scrollHeight, behavior: 'smooth' });
  }, []);
  return { ref, atLatest, onScroll, toLatest };
}
