/**
 * 服务端的文件（产物、知识库文档）交给预览抽屉：地址包成「怎么读」，顶部给「下载」。
 * 抽屉只认函数（FilePreview.tsx），本机会话里的文件由对话的来源给自己的读法与「打开」，两边走同一个抽屉。
 */
import { ButtonLink } from '@openai/apps-sdk-ui/components/Button';
import { Download } from '@openai/apps-sdk-ui/components/Icon';
import { requestBytes } from '../../api/http';
import type { PreviewFile } from './FilePreview';
import { pdfSource } from './previewKind';

export interface ServerFileUrls {
  readonly name: string;
  /** 原件地址：下载与原件预览都取它 */
  readonly url: string;
  /** PDF 阅读器取哪里；不给就是 url 后面接 /preview（产物预览接口：Word、PPT 转成的 PDF，PDF 原件原样） */
  readonly pdfUrl?: string;
  /** 给了就按 Word 渲染这里的 docx：知识库入库时把旧格式 Word（.doc）转成了 docx */
  readonly docxUrl?: string;
}

export function serverFile({ name, url, pdfUrl, docxUrl }: ServerFileUrls): PreviewFile {
  const pdf = pdfUrl ?? pdfSource(url);
  return {
    name,
    id: url,
    read: (signal) => requestBytes(url, signal),
    pdf: (signal) => requestBytes(pdf, signal),
    ...(docxUrl ? { docx: (signal?: AbortSignal) => requestBytes(docxUrl, signal) } : {}),
    actions: (
      <ButtonLink as="a" href={url} download={name} color="secondary" variant="outline" size="sm">
        <Download />
        下载
      </ButtonLink>
    ),
  };
}
