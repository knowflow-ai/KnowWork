/**
 * 认证访问的登录信息（接入方后端签的 JWT，服务端 channels/web_identity.py）从哪来：
 * - 嵌在接入方网页的小窗里：问外层的嵌入脚本要（widget.ts 再问接入方，见 protocol.ts）；
 * - 整页打开（例如手机 App 的 WebView 打开 /embed/<发布令牌>#token=<JWT>）：从地址的 # 后面取。# 后面的不发给服务器、
 *   不进访问日志；取完立刻从地址栏去掉，免得被复制出去（它只能用一次，用过也没用了）。
 */
import { EMBED_TOKEN, EMBED_TOKEN_REQUEST } from './protocol';

const WAIT_MS = 15_000;
export const NO_TOKEN = '没拿到登录信息：这个入口要从接入的系统里打开';

/** 没拿到登录信息（消息直接给人看） */
export class MissingLoginToken extends Error {
  constructor() {
    super(NO_TOKEN);
    this.name = 'MissingLoginToken';
  }
}

/** 地址 # 后面的 token；有就取走并从地址栏去掉。 */
export function tokenFromFragment(): string | null {
  const params = new URLSearchParams(window.location.hash.slice(1));
  const token = params.get('token');
  if (!token) return null;
  params.delete('token');
  const rest = params.toString();
  window.history.replaceState(
    null,
    '',
    `${window.location.pathname}${window.location.search}${rest ? `#${rest}` : ''}`,
  );
  return token;
}

/** 问外层的嵌入脚本要登录信息；不在别的网页里、或者等不到时报错（消息直接给人看）。 */
export function tokenFromHost(timeoutMs = WAIT_MS): Promise<string> {
  if (window.parent === window) return Promise.reject(new MissingLoginToken());
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => {
      window.removeEventListener('message', listen);
      reject(new MissingLoginToken());
    }, timeoutMs);
    function listen(event: MessageEvent) {
      // 只认外层那个窗口；接入方网页是哪个网站不用管（能嵌进来的只有允许的网站，网关按渠道设置管着）
      if (event.source !== window.parent) return;
      const data: unknown = event.data;
      if (typeof data !== 'object' || data === null) return;
      const message = data as { readonly type?: unknown; readonly token?: unknown };
      if (message.type !== EMBED_TOKEN) return;
      window.clearTimeout(timer);
      window.removeEventListener('message', listen);
      if (typeof message.token === 'string' && message.token) resolve(message.token);
      else reject(new MissingLoginToken());
    }
    window.addEventListener('message', listen);
    window.parent.postMessage({ type: EMBED_TOKEN_REQUEST }, '*');
  });
}

/** 认证访问拿登录信息：地址里带了就用，否则问外层。 */
export async function loginToken(): Promise<string> {
  return tokenFromFragment() ?? tokenFromHost();
}
