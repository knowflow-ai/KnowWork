/**
 * 画 PDF 的一页，照 DSH 官方预览（dsh-client-ui-sidebar-documentpreview 的 client/pdf/document.js 与 text.js，MIT）：
 * - 100% 是按 96 DPI 显示的大小；位图按「设备像素比 × 缩放」画，单页最多 16,777,216 像素；
 * - 文字层用 pdf.js 官方的 TextLayerBuilder：透明文字按原位叠在画面上，能选中、复制（复制时规范化文字），
 *   拖选越过段落末尾也不乱跳。只画 canvas 时整页像一张图片（2026-10-20 用户反馈 docx 预览选不中字）；
 * - 文字层只建一次，之后随画面宽度整体缩放，不必在每次缩放后重建。
 */
import './pdfjs';
import type { PDFDocumentProxy, PDFPageProxy, PageViewport } from 'pdfjs-dist';
import { TextLayerBuilder } from 'pdfjs-dist/legacy/web/pdf_viewer.mjs';

/** PDF 的长度单位是 1/72 英寸，屏幕按 96 DPI 算，100% 时乘这个比例。 */
export const PAGE_SCALE = 96 / 72;
const MAX_CANVAS_PIXELS = 16_777_216;

export interface PageSize {
  readonly width: number;
  readonly height: number;
}

export interface RenderTask {
  readonly promise: Promise<unknown>;
  cancel(): void;
}

export type TextRenderer = (page: PDFPageProxy, viewport: PageViewport) => RenderTask;

/** 把第 pageNumber 页画到 canvas 上，返回 100% 时的页面大小（CSS 像素）。renderText 给了就同时建文字层。 */
export async function renderPdfPage(
  pdf: PDFDocumentProxy,
  pageNumber: number,
  canvas: HTMLCanvasElement,
  signal: AbortSignal,
  pixelRatio: number,
  renderText?: TextRenderer,
): Promise<PageSize> {
  signal.throwIfAborted();
  const page = await pdf.getPage(pageNumber);
  try {
    signal.throwIfAborted();
    const viewport = page.getViewport({ scale: PAGE_SCALE });
    const ratio = Math.min(pixelRatio, Math.sqrt(MAX_CANVAS_PIXELS / (viewport.width * viewport.height)));
    canvas.width = Math.max(1, Math.floor(viewport.width * ratio));
    canvas.height = Math.max(1, Math.floor(viewport.height * ratio));
    const task = page.render({
      canvas,
      viewport,
      transform: ratio === 1 ? undefined : [ratio, 0, 0, ratio, 0, 0],
    });
    let text: RenderTask | undefined;
    let cancelled = false;
    const cancel = () => {
      if (cancelled) return;
      cancelled = true;
      task.cancel();
      text?.cancel();
    };
    signal.addEventListener('abort', cancel, { once: true });
    try {
      text = renderText?.(page, viewport);
      if (signal.aborted) cancel();
      await Promise.all([task.promise, text?.promise]);
      signal.throwIfAborted();
      return { width: viewport.width, height: viewport.height };
    } catch (error) {
      cancel();
      await Promise.allSettled([task.promise, text?.promise]);
      throw error;
    } finally {
      signal.removeEventListener('abort', cancel);
    }
  } finally {
    page.cleanup();
  }
}

/** 在 host（与画面同大小的绝对定位层）里建文字层；取消时一并断开尺寸监听、移除节点。 */
export function pdfTextRenderer(host: HTMLElement): TextRenderer {
  return (page, viewport) => {
    const layer = new TextLayerBuilder({ pdfPage: page });
    const container = layer.div;
    container.style.setProperty('--total-scale-factor', String(viewport.scale * viewport.userUnit));
    container.style.setProperty('--scale-round-x', '1px');
    container.style.setProperty('--scale-round-y', '1px');
    host.append(container);
    const resize = () => {
      container.style.scale = String(host.getBoundingClientRect().width / viewport.width);
    };
    const observer = typeof ResizeObserver === 'undefined' ? undefined : new ResizeObserver(resize);
    observer?.observe(host);
    resize();
    return {
      // 不传图片层（TextLayerImages，给图片做可选区用）：类型上标了必填，运行时可以省，官方预览也不传
      promise: layer.render({ viewport } as Parameters<TextLayerBuilder['render']>[0]),
      cancel() {
        observer?.disconnect();
        layer.cancel();
        container.remove();
      },
    };
  };
}
