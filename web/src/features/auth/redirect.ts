/** 只接受站内路径，防止 from 被拼成跳去外站。 */
export function safeRedirect(from: unknown): string {
  return typeof from === 'string' && from.startsWith('/') && !from.startsWith('//') ? from : '/chat';
}
