/**
 * 网页嵌入脚本（发布在 /embed/v1/widget.js，vite.widget.config.ts 单独打包，不依赖 React）。接入方页面这样引入：
 *
 *   <script src="https://<KnowWork 的地址>/embed/v1/widget.js" data-channel="<发布令牌>" async></script>
 *
 * 右下角（或左下角）一个按钮，点开是嵌入页（/embed/<发布令牌>，features/embed/EmbedPage.tsx）的小窗，手机上全屏。
 * - 样式放在 Shadow DOM 里：不受接入方页面的样式影响，也不影响它；
 * - 按钮的颜色、位置、名字从公开配置读（跨站读，服务端放开了来源）；入口关了就不显示按钮；
 * - 小窗第一次点开才建 iframe：接入方每翻一页都加载嵌入页、换会话令牌太浪费；
 * - 和嵌入页之间的消息只认嵌入页那个窗口、那个来源（features/embed/protocol.ts）；
 * - 接入方的脚本可以用 window.KnowWork.open() / close() / toggle() 控制小窗；
 * - 认证访问时嵌入页要登录信息（接入方后端签的 JWT），脚本按接入方给的办法取了交回去（只发给嵌入页那个来源）：
 *   data-token-url="/api/knowwork-token"（同站请求，带接入方自己的登录 Cookie，返回 JWT 原文或 {"token": "..."}），
 *   或者 window.KnowWorkSettings = { getToken: () => ... }（返回 JWT 或它的 Promise）。用到时才找，脚本先后顺序无所谓。
 */
import { EMBED_CLOSE, EMBED_READY, EMBED_TOKEN, EMBED_TOKEN_REQUEST } from '../features/embed/protocol';

export interface WidgetOptions {
  /** 发布令牌（嵌入代码里的 data-channel） */
  readonly key: string;
  /** KnowWork 的地址（脚本从哪里加载的） */
  readonly base: string;
  /** 认证访问取登录信息的地址（嵌入代码里的 data-token-url） */
  readonly tokenUrl?: string;
  readonly win?: Window;
  readonly fetchImpl?: typeof fetch;
}

/** 接入方页面上给的设置（可选）：认证访问时怎么取登录信息 */
export interface KnowWorkSettings {
  readonly getToken?: () => string | Promise<string>;
}

export interface Widget {
  readonly open: () => void;
  readonly close: () => void;
  readonly toggle: () => void;
  /** 读完公开配置（入口关了时按钮不出来） */
  readonly ready: Promise<void>;
}

const DEFAULT_COLOR = '#0d0d0d';
const COLOR = /^#[0-9A-Fa-f]{6}$/;

const STYLE = `
:host { all: initial; }
.button {
  position: fixed; bottom: 24px; z-index: 2147483000; width: 56px; height: 56px; border: 0; border-radius: 50%;
  display: flex; align-items: center; justify-content: center; cursor: pointer; color: #fff;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.18); transition: transform 0.15s ease;
}
.button:hover { transform: scale(1.05); }
.button:focus-visible { outline: 3px solid rgba(0, 0, 0, 0.35); outline-offset: 2px; }
.button[hidden] { display: none; }
.panel {
  position: fixed; bottom: 92px; z-index: 2147483000; width: 400px; height: min(640px, calc(100vh - 120px));
  border-radius: 16px; overflow: hidden; background: #fff; box-shadow: 0 8px 32px rgba(0, 0, 0, 0.2);
}
.panel[hidden] { display: none; }
.panel iframe { width: 100%; height: 100%; border: 0; display: block; }
.right { right: 24px; }
.left { left: 24px; }
@media (max-width: 640px) {
  .panel { inset: 0; width: 100%; height: 100%; border-radius: 0; }
  .open .button { display: none; }
}
`;

const ICON = `<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
  stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12Z"/></svg>`;

interface Config {
  readonly title?: unknown;
  readonly color?: unknown;
  readonly position?: unknown;
}

export function mount({ key, base, tokenUrl, win = window, fetchImpl = fetch }: WidgetOptions): Widget {
  const doc = win.document;
  const host = doc.createElement('div');
  host.setAttribute('data-knowwork-widget', '');
  const root = host.attachShadow({ mode: 'open' });
  const style = doc.createElement('style');
  style.textContent = STYLE;
  const shell = doc.createElement('div');
  const button = doc.createElement('button');
  button.type = 'button';
  button.className = 'button right';
  button.hidden = true;
  button.innerHTML = ICON;
  button.setAttribute('aria-expanded', 'false');
  button.setAttribute('aria-label', '打开智能助手');
  button.style.background = DEFAULT_COLOR;
  const panel = doc.createElement('div');
  panel.className = 'panel right';
  panel.hidden = true;
  shell.append(button, panel);
  root.append(style, shell);
  doc.body.append(host);

  const page = `${base}/embed/${encodeURIComponent(key)}`;
  let frame: HTMLIFrameElement | null = null;
  let title = '智能助手';

  const apply = (config: Config) => {
    if (typeof config.title === 'string' && config.title) {
      title = config.title;
      button.setAttribute('aria-label', `打开${title}`);
    }
    if (typeof config.color === 'string' && COLOR.test(config.color)) button.style.background = config.color;
    const side = config.position === 'left' ? 'left' : 'right';
    button.className = `button ${side}`;
    panel.className = `panel ${side}`;
  };

  const open = () => {
    if (!frame) {
      frame = doc.createElement('iframe');
      frame.src = page;
      frame.title = title;
      frame.allow = 'clipboard-write';
      panel.append(frame);
    }
    panel.hidden = false;
    shell.classList.add('open');
    button.setAttribute('aria-expanded', 'true');
  };
  const close = () => {
    panel.hidden = true;
    shell.classList.remove('open');
    button.setAttribute('aria-expanded', 'false');
  };
  const toggle = () => (panel.hidden ? open() : close());

  button.addEventListener('click', toggle);
  win.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !panel.hidden) close();
  });
  /** 认证访问的登录信息：先看接入方页面上的 getToken，再看 data-token-url；都没有或取不到给 null */
  const token = async (): Promise<string | null> => {
    const getToken = win.KnowWorkSettings?.getToken;
    if (getToken) return (await getToken()) || null;
    if (!tokenUrl) return null;
    const response = await fetchImpl(tokenUrl, { credentials: 'include' });
    if (!response.ok) return null;
    const text = (await response.text()).trim();
    try {
      const parsed: unknown = JSON.parse(text);
      if (typeof parsed === 'object' && parsed !== null && 'token' in parsed) {
        const value = (parsed as { readonly token?: unknown }).token;
        return typeof value === 'string' ? value : null;
      }
    } catch {
      // 不是 JSON：就是 JWT 原文
    }
    return text || null;
  };
  const answerToken = (target: Window) => {
    void token()
      .catch(() => null)
      .then((value) => target.postMessage({ type: EMBED_TOKEN, token: value }, base));
  };

  win.addEventListener('message', (event: MessageEvent) => {
    // 只认嵌入页那个窗口、那个来源
    if (event.origin !== base || !frame?.contentWindow || event.source !== frame.contentWindow) return;
    const data: unknown = event.data;
    if (typeof data !== 'object' || data === null) return;
    const message = data as { readonly type?: unknown } & Config;
    if (message.type === EMBED_READY) apply(message);
    else if (message.type === EMBED_CLOSE) close();
    else if (message.type === EMBED_TOKEN_REQUEST) answerToken(frame.contentWindow);
  });

  const ready = fetchImpl(`${base}/api/embed/v1/channels/${encodeURIComponent(key)}`, { credentials: 'omit' })
    .then(async (response) => {
      if (!response.ok) return; // 入口关了或不存在：不出按钮
      apply((await response.json()) as Config);
      button.hidden = false;
    })
    .catch(() => undefined); // 连不上 KnowWork：不出按钮，也不打扰接入方的页面

  return { open, close, toggle, ready };
}

declare global {
  interface Window {
    KnowWork?: Pick<Widget, 'open' | 'close' | 'toggle'>;
    KnowWorkSettings?: KnowWorkSettings;
  }
}

// 作为 <script> 引入时自己装上；同一页面引了两次只装一次
const script = typeof document === 'undefined' ? null : document.currentScript;
if (script instanceof HTMLScriptElement && script.dataset.channel && !window.KnowWork) {
  const widget = mount({
    key: script.dataset.channel,
    base: new URL(script.src).origin,
    tokenUrl: script.dataset.tokenUrl,
  });
  window.KnowWork = { open: widget.open, close: widget.close, toggle: widget.toggle };
}
