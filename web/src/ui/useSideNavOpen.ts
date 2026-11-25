/**
 * 页面二级侧栏（ui/SideNav）开没开：每个人在自己浏览器里记着，name 区分页面（知识库、指标各记各的）；
 * 读写不了（隐私模式、存储被禁）照常用，默认开。
 */
import { useState } from 'react';

const KEY_PREFIX = 'kw.sideNav.';

function load(name: string): boolean {
  try {
    return window.localStorage.getItem(KEY_PREFIX + name) !== 'closed';
  } catch {
    return true;
  }
}

export function useSideNavOpen(name: string): readonly [boolean, (open: boolean) => void] {
  const [open, setOpen] = useState(() => load(name));
  const change = (next: boolean) => {
    setOpen(next);
    try {
      window.localStorage.setItem(KEY_PREFIX + name, next ? 'open' : 'closed');
    } catch {
      // 存不下就只管这一次
    }
  };
  return [open, change] as const;
}
