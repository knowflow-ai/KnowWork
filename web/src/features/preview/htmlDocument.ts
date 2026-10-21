/**
 * HTML 产物的两种预览文档，照搬 DSH 官方 dsh-client-ui-sidebar-documentpreview 的 html/basic-document 与 HtmlFrame：
 * - 静态（默认）：DOMPurify 净化整篇文档，去掉链接、外部资源和嵌套框架，再在 head 最前面放一条严格的 CSP，
 *   脚本、网络请求、表单一律不跑，只留内联样式和 data: 图片；放进不给任何权限的 iframe（sandbox=""）。
 * - 交互：原文放进 Blob，iframe 只给 allow-scripts、不给 allow-same-origin，页面拿到的是不透明的源，
 *   读不到我们的 Cookie、接口和父页面；外链脚本（如 CDN 上的图表库）由浏览器按网络规则加载。
 *   官方还会把同目录下引用的 .js/.css 一起打包，我们的产物是单个文件，没有这一步。
 */
import DOMPurify from 'dompurify';

const STATIC_POLICY =
  "default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; " +
  "media-src data:; connect-src 'none'; frame-src 'none'; form-action 'none'; base-uri 'none'";

const FORBID_TAGS = [
  'noscript',
  'base',
  'link',
  'meta',
  'iframe',
  'frame',
  'object',
  'embed',
  'set',
  'animate',
  'animateMotion',
  'animateTransform',
];

export function decodeHtml(data: ArrayBuffer): string {
  return new TextDecoder('utf-8').decode(data);
}

/** 静态预览用的完整文档：净化后重新解析，把 CSP 放在 head 第一位，浏览器加载任何资源之前它就生效。 */
export function staticHtmlDocument(data: ArrayBuffer): string {
  const clean = DOMPurify.sanitize(decodeHtml(data), {
    WHOLE_DOCUMENT: true,
    FORBID_TAGS,
    FORBID_ATTR: ['href', 'xlink:href'],
  });
  const parsed = new DOMParser().parseFromString(clean, 'text/html');
  const policy = parsed.createElement('meta');
  policy.setAttribute('http-equiv', 'Content-Security-Policy');
  policy.setAttribute('content', STATIC_POLICY);
  parsed.head.prepend(policy);
  return `<!doctype html>${parsed.documentElement.outerHTML}`;
}
