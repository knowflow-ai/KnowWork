/**
 * 网页嵌入页的会话令牌（服务端 channels/web.py）：嵌入页拿访客 id 换到它，之后各接口都带
 * `Authorization: Bearer emb_...`。
 *
 * 这是「前端不存令牌」唯一的例外：嵌在别的网站里时浏览器不带我们的 Cookie（跨站 iframe），只能自己带。
 * 所以只放在这个模块的内存里，不写 localStorage、sessionStorage、Cookie，页面关了就没了，再打开重新换；
 * 两小时过期，嵌入页用着就续。没有嵌入会话时（平常的网页端）什么都不加，照旧用 Cookie。
 */

let token: string | null = null;

export function setEmbedToken(value: string | null): void {
  token = value;
}

export function embedToken(): string | null {
  return token;
}

/**
 * 发请求时要带的：有嵌入令牌就带上它、不带 Cookie（分享链接在同一个站点整页打开时，不让浏览器里登录着的人混进来）；
 * 没有就照旧同源带 Cookie。
 */
export function credentialsFor(headers: Record<string, string> = {}): {
  readonly headers: Record<string, string>;
  readonly credentials: RequestCredentials;
} {
  if (!token) return { headers, credentials: 'same-origin' };
  return { headers: { ...headers, Authorization: `Bearer ${token}` }, credentials: 'omit' };
}
