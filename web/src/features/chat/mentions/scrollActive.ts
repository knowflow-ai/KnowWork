/**
 * 菜单（@ 与 /）里高亮的那一行：
 * - 用键盘移动时滚进可见区，只滚到刚好露出来（nearest，同 ui/Tree.tsx）；
 * - 鼠标只有真的移动了才改高亮。列表因为按键滚动、鼠标停着不动时，浏览器也会对新滑到鼠标底下的那一行发「移进来」，
 *   用它改高亮的话，往上按一下、列表一滚，高亮又被拉回鼠标底下那一行，看着像往上滚不动（2026-11-22 用户撞上）。
 *   所以不用 mouseenter，用 mousemove，并且鼠标位置没变的不算。
 */
import { useEffect, useRef, type MouseEvent } from 'react';

export function useScrollIntoView<T extends HTMLElement>(active: boolean) {
  const ref = useRef<T>(null);
  useEffect(() => {
    // jsdom 里没有 scrollIntoView
    if (active) ref.current?.scrollIntoView?.({ block: 'nearest' });
  }, [active]);
  return ref;
}

let last: { readonly x: number; readonly y: number } | null = null;

/** 鼠标真的动了（和上一次的位置不同）才返回 true */
export function pointerMoved(event: MouseEvent): boolean {
  const moved = last === null || last.x !== event.clientX || last.y !== event.clientY;
  last = { x: event.clientX, y: event.clientY };
  return moved;
}
