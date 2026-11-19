/**
 * 媒体查询现在是否成立，窗口变了跟着变。没有 matchMedia 的环境（测试的 jsdom、很老的浏览器）当作不成立。
 */
import { useSyncExternalStore } from 'react';

export function useMediaQuery(query: string): boolean {
  return useSyncExternalStore(
    (onChange) => {
      const media = typeof window.matchMedia === 'function' ? window.matchMedia(query) : null;
      media?.addEventListener('change', onChange);
      return () => media?.removeEventListener('change', onChange);
    },
    () => (typeof window.matchMedia === 'function' ? window.matchMedia(query).matches : false),
    () => false,
  );
}
