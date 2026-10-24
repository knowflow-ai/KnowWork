/**
 * 文件怎么预览（照 DSH 官方预览的组合）：
 * - Word（.docx）在浏览器里直接渲染原件（docx-preview），保留原来的样式，知识库引用按段落整段高亮；
 * - 旧格式 Word、PPT 由后端在沙箱里转成 PDF，和 PDF 一起交给 PDF 阅读器（知识库里的 .doc 入库时转成了 docx，
 *   调用方给 docxUrl，按 Word 渲染，见 FilePreview）；
 * - 表格在浏览器里按工作表渲染；HTML 在隔离的 iframe 里渲染；Markdown、文本、图片直接渲染；
 * - 其余类型只给下载。
 */
export type PreviewKind = 'pdf' | 'docx' | 'sheet' | 'html' | 'markdown' | 'text' | 'image' | 'none';

const CONVERTED = ['doc', 'odt', 'rtf', 'ppt', 'pptx', 'odp', 'pdf'];
const SHEETS = ['xlsx', 'xls', 'csv', 'tsv', 'ods'];
const TEXTS = ['txt', 'log', 'json', 'yaml', 'yml', 'xml', 'py', 'js', 'ts', 'sql', 'sh', 'css'];
const IMAGES = ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp'];

export function extensionOf(name: string): string {
  return name.includes('.') ? (name.split('.').pop()?.toLowerCase() ?? '') : '';
}

export function previewKindOf(name: string): PreviewKind {
  const ext = extensionOf(name);
  if (ext === 'docx') return 'docx';
  if (CONVERTED.includes(ext)) return 'pdf';
  if (SHEETS.includes(ext)) return 'sheet';
  if (ext === 'html' || ext === 'htm') return 'html';
  if (ext === 'md' || ext === 'markdown') return 'markdown';
  if (TEXTS.includes(ext)) return 'text';
  if (IMAGES.includes(ext)) return 'image';
  return 'none';
}

/** PDF 阅读器取哪里：Word、PPT 取预览接口（服务端转换后的 PDF），PDF 原件也走它（原样返回）。 */
export function pdfSource(url: string): string {
  return `${url}/preview`;
}
