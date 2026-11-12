/**
 * 嵌入页（EmbedPage.tsx）与嵌入脚本（src/embed/widget.ts）之间的消息：
 * - 嵌入页 → 脚本：名字与外观（READY）、收起小窗（CLOSE）、认证访问要登录信息（TOKEN_REQUEST）；
 * - 脚本 → 嵌入页：登录信息（TOKEN，接入方后端签的 JWT），只发给嵌入页那个来源。
 * 名字、外观本来就公开；JWT 只能用一次、几分钟就过期。
 */
export const EMBED_READY = 'knowwork:ready';
export const EMBED_CLOSE = 'knowwork:close';
export const EMBED_TOKEN_REQUEST = 'knowwork:token-request';
export const EMBED_TOKEN = 'knowwork:token';
