/**
 * 别人从外面访问本服务用的地址（拼嵌入代码、分享链接用）。
 *
 * 普通浏览器里就是页面自己的地址。页面被外壳（例如原生应用）用自定义协议加载时，页面地址不是 http(s)，
 * 外壳经 preload 在 window.knowworkShell.origin 给出它连着的服务地址；只认 http(s) 的值。
 */
interface Shell {
  readonly origin?: unknown;
}

export function publicOrigin(): string {
  const shell = (window as { knowworkShell?: Shell }).knowworkShell;
  const origin = typeof shell?.origin === 'string' ? shell.origin : '';
  return /^https?:\/\/[^/\s]+$/.test(origin) ? origin : window.location.origin;
}

/**
 * 要在新窗口打开的站内页面（接口文档、从评测里打开一段对话这类）：链接写成服务端的完整地址。
 * 浏览器里和站内地址一样开新标签页；外壳（桌面端）照 DSH 官方桌面版不开站内新窗口，只把 http(s) 地址交给系统浏览器，
 * 写成完整地址就由系统浏览器打开，不会点了没反应。path 以 / 开头。
 */
export function newWindowUrl(path: string): string {
  return `${publicOrigin()}${path}`;
}
