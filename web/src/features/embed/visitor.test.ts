import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { visitorId } from './visitor';

describe('公开访问的访客 id', () => {
  beforeEach(() => window.localStorage.clear());
  afterEach(() => vi.unstubAllGlobals());

  it('纯 HTTP、非 localhost 的地址下（浏览器不给 randomUUID）照样生成，并存下来接着用', () => {
    // 非安全上下文里 crypto.randomUUID 不存在，getRandomValues 仍然有
    vi.stubGlobal('crypto', { getRandomValues: crypto.getRandomValues.bind(crypto) });
    const id = visitorId();
    expect(id).toMatch(/^[0-9a-f]{32}$/);
    expect(visitorId()).toBe(id);
  });

  it('存过合法的 id 就沿用，不合法的换新的', () => {
    window.localStorage.setItem('kw.embed.visitor', 'abcdefghijklmnop');
    expect(visitorId()).toBe('abcdefghijklmnop');
    window.localStorage.setItem('kw.embed.visitor', 'bad id!');
    expect(visitorId()).toMatch(/^[0-9a-f]{32}$/);
  });
});
