/**
 * 公开访问的访客 id：随机生成，存在本浏览器里，换会话令牌时带上，用来接着上一段对话（服务端按它分开各个访客的会话）。
 * 不是凭据；嵌在别的网站里时浏览器本来就按那个网站分开存。存储被禁时只在这一页有效。
 */
const KEY = 'kw.embed.visitor';
const PATTERN = /^[A-Za-z0-9_-]{16,64}$/;

export function visitorId(): string {
  try {
    const saved = window.localStorage.getItem(KEY);
    if (saved && PATTERN.test(saved)) return saved;
  } catch {
    // 存储被禁：下面生成一个只在这一页有效的
  }
  const id = crypto.randomUUID().replaceAll('-', '');
  try {
    window.localStorage.setItem(KEY, id);
  } catch {
    // 存不下就只在这一页有效
  }
  return id;
}
