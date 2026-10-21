import '@testing-library/jest-dom/vitest';
import { afterEach } from 'vitest';
import { cleanup, configure } from '@testing-library/react';
import { useToasts } from '../ui/toastStore';

// 全量并行跑（尤其带覆盖率）时机器很忙，异步查找默认 1 秒不够，放宽到 5 秒。
configure({ asyncUtilTimeout: 5000 });

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  useToasts.getState().clear();
});

// jsdom 没有 matchMedia，组件库的悬停提示、气泡按媒体查询判断是否支持悬停。
if (!window.matchMedia) {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => undefined,
      removeListener: () => undefined,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      dispatchEvent: () => false,
    }),
  });
}

// jsdom 没有 ResizeObserver，Radix 的浮层定位要用它。
if (!('ResizeObserver' in window)) {
  class ResizeObserverStub {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  Object.defineProperty(window, 'ResizeObserver', { writable: true, value: ResizeObserverStub });
}

// jsdom 不实现滚动，消息列表会调用它。
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => undefined;
}

// jsdom 不做布局，元素尺寸、视口都是 0。组件库的气泡和悬停提示（Radix Popper 的 hideWhenDetached）会把
// 0 尺寸、贴在视口边上的触发元素当成已滚出视口（还要扣掉 20px 的避让边距）而隐藏内容，按角色就找不到了。
// 给视口一个桌面尺寸，没有尺寸的元素当作视口里 (40, 40) 处一个 100×20 的块。
Object.defineProperty(document.documentElement, 'clientWidth', { configurable: true, get: () => 1280 });
Object.defineProperty(document.documentElement, 'clientHeight', { configurable: true, get: () => 800 });
const originalRect = Element.prototype.getBoundingClientRect;
Element.prototype.getBoundingClientRect = function getBoundingClientRect(this: Element) {
  const rect = originalRect.call(this);
  if (rect.width || rect.height) return rect;
  return DOMRect.fromRect({ x: 40, y: 40, width: 100, height: 20 });
};
