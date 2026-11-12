import { afterEach, describe, expect, it, vi } from 'vitest';
import { mount } from './widget';
import { EMBED_CLOSE, EMBED_READY, EMBED_TOKEN, EMBED_TOKEN_REQUEST } from '../features/embed/protocol';

const BASE = 'https://kw.example.com';
const KEY = 'ws1~abcdefghijklmnopqrstuvwx';

afterEach(() => {
  document.body.innerHTML = '';
  vi.restoreAllMocks();
});

/** jsdom 里 Shadow DOM 中的 iframe 没有 contentWindow：给它一个假的窗口，嵌入页发来的消息以它为来源 */
function fakeFrameWindow(): Window {
  const target = { postMessage: vi.fn() } as unknown as Window;
  vi.spyOn(HTMLIFrameElement.prototype, 'contentWindow', 'get').mockReturnValue(target);
  return target;
}

function config(status = 200, body: unknown = { title: 'AI 问数', color: '#112233', position: 'left' }) {
  return vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status }));
}

const parts = () => {
  const root = document.querySelector('[data-knowwork-widget]')?.shadowRoot;
  return {
    button: root?.querySelector('button') as HTMLButtonElement,
    panel: root?.querySelector('.panel') as HTMLDivElement,
    frame: () => root?.querySelector('iframe') ?? null,
  };
};

describe('网页嵌入脚本', () => {
  it('读公开配置给按钮配色、定位，第一次点开才建小窗', async () => {
    const fetchImpl = config();
    const widget = mount({ key: KEY, base: BASE, fetchImpl });
    await widget.ready;
    expect(fetchImpl).toHaveBeenCalledWith(`${BASE}/api/embed/v1/channels/${encodeURIComponent(KEY)}`, {
      credentials: 'omit',
    });
    const { button, panel, frame } = parts();
    expect(button.hidden).toBe(false);
    expect(button.style.background).toBe('rgb(17, 34, 51)');
    expect(button.className).toBe('button left');
    expect(button.getAttribute('aria-label')).toBe('打开AI 问数');
    expect(frame()).toBeNull();
    button.click();
    expect(panel.hidden).toBe(false);
    expect(frame()?.src).toBe(`${BASE}/embed/${encodeURIComponent(KEY)}`);
    button.click();
    expect(panel.hidden).toBe(true);
  });

  it('入口关了或连不上：不出按钮', async () => {
    const closed = mount({ key: KEY, base: BASE, fetchImpl: config(404, { detail: '关了' }) });
    await closed.ready;
    expect(parts().button.hidden).toBe(true);
    document.body.innerHTML = '';
    const unreachable = mount({
      key: KEY,
      base: BASE,
      fetchImpl: vi.fn().mockRejectedValue(new TypeError('fail')),
    });
    await unreachable.ready;
    expect(parts().button.hidden).toBe(true);
  });

  it('只认嵌入页那个窗口、那个来源发来的消息', async () => {
    const source = fakeFrameWindow();
    const widget = mount({ key: KEY, base: BASE, fetchImpl: config() });
    await widget.ready;
    widget.open();
    const { panel } = parts();
    // 来源对、窗口不对（别的 iframe）：不理
    window.dispatchEvent(
      new MessageEvent('message', { data: { type: EMBED_CLOSE }, origin: BASE, source: window }),
    );
    expect(panel.hidden).toBe(false);
    // 别的来源说收起：不理
    window.dispatchEvent(
      new MessageEvent('message', { data: { type: EMBED_CLOSE }, origin: 'https://evil.example', source }),
    );
    expect(panel.hidden).toBe(false);
    // 嵌入页说收起：收起
    window.dispatchEvent(new MessageEvent('message', { data: { type: EMBED_CLOSE }, origin: BASE, source }));
    expect(panel.hidden).toBe(true);
    // 嵌入页告诉名字与外观
    window.dispatchEvent(
      new MessageEvent('message', {
        data: { type: EMBED_READY, color: '#445566', position: 'right' },
        origin: BASE,
        source,
      }),
    );
    expect(parts().button.className).toBe('button right');
  });

  it('按 Esc 收起', async () => {
    const widget = mount({ key: KEY, base: BASE, fetchImpl: config() });
    await widget.ready;
    widget.open();
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    expect(parts().panel.hidden).toBe(true);
  });
});

describe('认证访问的登录信息', () => {
  const TOKEN_URL = '/api/knowwork-token';
  const fetcher = (token: Response) =>
    vi.fn(async (url: RequestInfo | URL) =>
      String(url) === TOKEN_URL ? token : new Response(JSON.stringify({ title: 'BI 助手' }), { status: 200 }),
    );

  async function ask(build: () => ReturnType<typeof mount>) {
    const target = fakeFrameWindow();
    const widget = build();
    await widget.ready;
    widget.open();
    const post = vi.mocked(target.postMessage);
    window.dispatchEvent(
      new MessageEvent('message', { data: { type: EMBED_TOKEN_REQUEST }, origin: BASE, source: target }),
    );
    await vi.waitFor(() => expect(post).toHaveBeenCalled());
    return post;
  }

  afterEach(() => {
    delete window.KnowWorkSettings;
  });

  it('按 data-token-url 取（带接入方自己的 Cookie），原文、JSON 都认，只发给嵌入页那个来源', async () => {
    const fetchImpl = fetcher(new Response('jwt-1'));
    const post = await ask(() => mount({ key: KEY, base: BASE, tokenUrl: TOKEN_URL, fetchImpl }));
    expect(post).toHaveBeenCalledWith({ type: EMBED_TOKEN, token: 'jwt-1' }, BASE);
    expect(fetchImpl).toHaveBeenCalledWith(TOKEN_URL, { credentials: 'include' });
    document.body.innerHTML = '';
    const json = await ask(() =>
      mount({
        key: KEY,
        base: BASE,
        tokenUrl: TOKEN_URL,
        fetchImpl: fetcher(new Response('{"token":"jwt-2"}')),
      }),
    );
    expect(json).toHaveBeenCalledWith({ type: EMBED_TOKEN, token: 'jwt-2' }, BASE);
  });

  it('页面上给了 getToken 就用它；都没有、取不到时交回空的', async () => {
    window.KnowWorkSettings = { getToken: async () => 'jwt-3' };
    const post = await ask(() =>
      mount({ key: KEY, base: BASE, tokenUrl: TOKEN_URL, fetchImpl: fetcher(new Response('x')) }),
    );
    expect(post).toHaveBeenCalledWith({ type: EMBED_TOKEN, token: 'jwt-3' }, BASE);
    delete window.KnowWorkSettings;
    document.body.innerHTML = '';
    const none = await ask(() => mount({ key: KEY, base: BASE, fetchImpl: fetcher(new Response('x')) }));
    expect(none).toHaveBeenCalledWith({ type: EMBED_TOKEN, token: null }, BASE);
    document.body.innerHTML = '';
    const failed = await ask(() =>
      mount({
        key: KEY,
        base: BASE,
        tokenUrl: TOKEN_URL,
        fetchImpl: fetcher(new Response('no', { status: 401 })),
      }),
    );
    expect(failed).toHaveBeenCalledWith({ type: EMBED_TOKEN, token: null }, BASE);
  });
});
