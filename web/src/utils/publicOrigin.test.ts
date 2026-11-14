import { afterEach, describe, expect, it } from 'vitest';
import { newWindowUrl, publicOrigin } from './publicOrigin';

afterEach(() => {
  delete (window as { knowworkShell?: unknown }).knowworkShell;
});

describe('publicOrigin', () => {
  it('普通浏览器里就是页面自己的地址', () => {
    expect(publicOrigin()).toBe(window.location.origin);
  });

  it('被外壳用自定义协议加载时，用外壳给的服务地址', () => {
    (window as { knowworkShell?: unknown }).knowworkShell = { origin: 'https://kw.example.com' };
    expect(publicOrigin()).toBe('https://kw.example.com');
  });

  it('外壳给的不是 http(s) 地址就不认', () => {
    (window as { knowworkShell?: unknown }).knowworkShell = { origin: 'javascript:alert(1)' };
    expect(publicOrigin()).toBe(window.location.origin);
  });
});

describe('newWindowUrl', () => {
  it('写成服务端的完整地址：浏览器里是页面自己的地址，外壳里是它连着的服务地址（桌面端交给系统浏览器打开）', () => {
    expect(newWindowUrl('/api/docs')).toBe(`${window.location.origin}/api/docs`);
    (window as { knowworkShell?: unknown }).knowworkShell = { origin: 'https://kw.example.com' };
    expect(newWindowUrl('/chat/c_1')).toBe('https://kw.example.com/chat/c_1');
  });
});
