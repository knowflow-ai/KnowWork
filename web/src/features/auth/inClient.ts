/**
 * 在企业微信等客户端的内置浏览器里打开登录页：浏览器标识命中某个登录方式的 inClientAgents，就直接走它的免登，
 * 员工不用再点。一分钟内只自动跳一次：会话没留住又回到登录页时不再兜圈子；带 ?local=1 不跳，留给管理员用账号密码兜底。
 */
import type { LoginMethod } from '../../api/auth';

const KEY = 'kw-in-client-login';
const ONCE_PER_MS = 60_000;

/** 跳转用的入口（测试里换掉，jsdom 不能真的跳）。 */
export const navigation = {
  go: (url: string) => window.location.assign(url),
};

export function inClientMethod(methods: readonly LoginMethod[], userAgent: string): LoginMethod | undefined {
  const agent = userAgent.toLowerCase();
  return methods.find(
    (method) =>
      method.style === 'redirect' &&
      method.inClientAgents.some((marker) => agent.includes(marker.toLowerCase())),
  );
}

function triedRecently(now: number): boolean {
  try {
    const last = window.sessionStorage.getItem(KEY);
    return last !== null && now - Number(last) < ONCE_PER_MS;
  } catch {
    return false;
  }
}

function remember(now: number): void {
  try {
    window.sessionStorage.setItem(KEY, String(now));
  } catch {
    // 存不了（隐私模式等）就不记：失败时回调停在出错页，不会回到这里兜圈子
  }
}

/** 要自动跳就返回带 in_client=1 的入口地址并记下这一次，不跳返回 null。 */
export function inClientTarget(
  methods: readonly LoginMethod[],
  userAgent: string,
  search: string,
  now = Date.now(),
): string | null {
  if (new URLSearchParams(search).get('local') === '1') return null;
  const method = inClientMethod(methods, userAgent);
  if (!method || triedRecently(now)) return null;
  remember(now);
  return `${method.url}${method.url.includes('?') ? '&' : '?'}in_client=1`;
}
