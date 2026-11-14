/**
 * 单点登录（OIDC、企业微信扫码等）回来后回到原来的页面。
 *
 * 账号密码登录在本页完成，直接跳回 from；单点登录要先跳去身份中台，回调后服务端固定跳到首页，原来要去的页面
 * （例如桌面端打开的登录确认页，地址里带着授权参数）就丢了。跳走前把它记在 sessionStorage（同一个标签页里跨跳转
 * 还在，关了就没了），回来进到受保护页面时取出来跳过去，跳完清掉。只记站内路径，默认首页不记。
 */
import { safeRedirect } from './redirect';

const KEY = 'kw-after-login';
const DEFAULT = '/chat';

export function rememberAfterLogin(path: string): void {
  const safe = safeRedirect(path);
  if (safe === DEFAULT || safe !== path) return;
  try {
    window.sessionStorage.setItem(KEY, safe);
  } catch {
    // 浏览器不让写（隐私模式等）：回来落在首页，不影响登录
  }
}

/** 只读不删：渲染里调用（严格模式会渲染两次，删要放到 effect 里，见 clearAfterLogin）。 */
export function peekAfterLogin(): string | null {
  try {
    const saved = window.sessionStorage.getItem(KEY);
    if (saved === null) return null;
    const safe = safeRedirect(saved);
    return safe === saved && safe !== DEFAULT ? safe : null;
  } catch {
    return null;
  }
}

export function clearAfterLogin(): void {
  try {
    window.sessionStorage.removeItem(KEY);
  } catch {
    // 同上
  }
}
