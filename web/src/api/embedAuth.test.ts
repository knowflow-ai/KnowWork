import { afterEach, describe, expect, it, vi } from 'vitest';
import { credentialsFor, setEmbedToken } from './embedAuth';
import { request } from './http';
import { jsonResponse } from '../test/render';

afterEach(() => {
  setEmbedToken(null);
  vi.unstubAllGlobals();
});

describe('网页嵌入的会话令牌', () => {
  it('平常的网页端照旧同源带 Cookie，不加令牌', () => {
    expect(credentialsFor({ Accept: 'x' })).toEqual({ headers: { Accept: 'x' }, credentials: 'same-origin' });
  });

  it('有嵌入会话时带上令牌、不带 Cookie', async () => {
    setEmbedToken('emb_abc.def');
    const fetch = vi.fn().mockResolvedValue(jsonResponse({ ok: true }));
    vi.stubGlobal('fetch', fetch);
    await request('/api/workspaces/ws/conversations');
    const init = fetch.mock.calls[0]?.[1] as RequestInit;
    expect(init.credentials).toBe('omit');
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer emb_abc.def');
  });

  it('令牌只在内存里，不写进浏览器存储', () => {
    const setItem = vi.spyOn(Storage.prototype, 'setItem');
    setEmbedToken('emb_abc.def');
    expect(setItem).not.toHaveBeenCalled();
    setItem.mockRestore();
  });
});
