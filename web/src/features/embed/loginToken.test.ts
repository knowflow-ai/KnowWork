import { afterEach, describe, expect, it, vi } from 'vitest';
import { NO_TOKEN, tokenFromFragment, tokenFromHost } from './loginToken';
import { EMBED_TOKEN, EMBED_TOKEN_REQUEST } from './protocol';

afterEach(() => {
  vi.restoreAllMocks();
  window.history.replaceState(null, '', '/');
});

describe('认证访问的登录信息', () => {
  it('从地址 # 后面取走，并从地址栏去掉', () => {
    window.history.replaceState(null, '', '/embed/k?x=1#token=abc.def.ghi&lang=zh');
    expect(tokenFromFragment()).toBe('abc.def.ghi');
    expect(window.location.hash).toBe('#lang=zh');
    expect(window.location.search).toBe('?x=1');
    expect(tokenFromFragment()).toBeNull();
  });

  it('整页打开又没带：说清楚要从接入的系统里打开', async () => {
    await expect(tokenFromHost()).rejects.toThrow(NO_TOKEN);
  });

  it('嵌在别的网页里：问外层要，只认外层那个窗口回的', async () => {
    const parent = { postMessage: vi.fn() } as unknown as Window;
    vi.spyOn(window, 'parent', 'get').mockReturnValue(parent);
    const waiting = tokenFromHost(1000);
    expect(parent.postMessage).toHaveBeenCalledWith({ type: EMBED_TOKEN_REQUEST }, '*');
    const stranger = {} as Window;
    window.dispatchEvent(
      new MessageEvent('message', { data: { type: EMBED_TOKEN, token: 'evil' }, source: stranger }),
    );
    window.dispatchEvent(
      new MessageEvent('message', { data: { type: EMBED_TOKEN, token: 'jwt-ok' }, source: parent }),
    );
    await expect(waiting).resolves.toBe('jwt-ok');
  });

  it('外层说取不到、或者等不到：报错', async () => {
    const parent = { postMessage: vi.fn() } as unknown as Window;
    vi.spyOn(window, 'parent', 'get').mockReturnValue(parent);
    const refused = tokenFromHost(1000);
    window.dispatchEvent(
      new MessageEvent('message', { data: { type: EMBED_TOKEN, token: null }, source: parent }),
    );
    await expect(refused).rejects.toThrow(NO_TOKEN);
    await expect(tokenFromHost(10)).rejects.toThrow(NO_TOKEN);
  });
});
