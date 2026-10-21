/**
 * PDF 的一页，照 DSH 官方预览（client/pdf/pdf.js 的 PdfPage，MIT）：
 * - 滚到视口上下各一屏的范围内才画（IntersectionObserver），没画之前是 3:4 的灰色占位；
 * - 缩放停下后，只重画还在视口附近的页面；先画到离屏 canvas 再一次拷过来，重画期间旧画面不闪；
 * - 文字层只在第一次画时建，之后随画面宽度缩放。
 */
import { useEffect, useRef, useState } from 'react';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import type { PDFDocumentProxy } from 'pdfjs-dist';
import { pdfTextRenderer, renderPdfPage, type RenderTask } from './pdfRender';

export function PdfPage({
  pdf,
  page,
  requested: initiallyRequested,
  signal,
  zoom,
  onWidth,
}: {
  readonly pdf: PDFDocumentProxy;
  readonly page: number;
  /** 一开始就画（第一页）。 */
  readonly requested: boolean;
  readonly signal: AbortSignal;
  readonly zoom: number;
  /** 量出 100% 时的页宽后告诉外层，给适应宽度用（只有第一页给）。 */
  readonly onWidth?: (width: number) => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const text = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState<number>();
  const [requested, setRequested] = useState(initiallyRequested);
  const [visible, setVisible] = useState(initiallyRequested);
  const renderedZoom = useRef<number>(undefined);
  const pendingRender = useRef<Promise<void>>(Promise.resolve());
  const textTask = useRef<RenderTask>(undefined);
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const node = ready ? canvas.current : host.current;
    if (!node || typeof IntersectionObserver === 'undefined') {
      setRequested(true);
      setVisible(true);
      return undefined;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        const intersects = entries.some((entry) => entry.isIntersecting);
        setVisible(intersects);
        if (intersects) setRequested(true);
      },
      { rootMargin: '100% 0px' },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [ready]);

  useEffect(() => () => textTask.current?.cancel(), []);

  useEffect(() => {
    const node = canvas.current;
    if (!requested || !node) return undefined;
    setFailed(false);
    if (renderedZoom.current !== undefined && (!visible || renderedZoom.current === zoom)) return undefined;
    const lifetime = new AbortController();
    const renderSignal = AbortSignal.any([lifetime.signal, signal]);
    let newText: RenderTask | undefined;
    const textHost = text.current;
    pendingRender.current = pendingRender.current
      .then(async () => {
        renderSignal.throwIfAborted();
        const buffer = node.ownerDocument.createElement('canvas');
        try {
          const renderText =
            textTask.current === undefined && textHost
              ? (...args: Parameters<ReturnType<typeof pdfTextRenderer>>) => {
                  newText = pdfTextRenderer(textHost)(...args);
                  return newText;
                }
              : undefined;
          const size = await renderPdfPage(
            pdf,
            page,
            buffer,
            renderSignal,
            window.devicePixelRatio * zoom,
            renderText,
          );
          renderSignal.throwIfAborted();
          const context = node.getContext('2d');
          if (context === null) throw new Error('PDF 画布拿不到 2D 上下文');
          node.width = buffer.width;
          node.height = buffer.height;
          context.drawImage(buffer, 0, 0);
          textTask.current ??= newText;
          newText = undefined;
          renderedZoom.current = zoom;
          setWidth(size.width);
          onWidth?.(size.width);
          setReady(true);
        } finally {
          buffer.width = 0;
          buffer.height = 0;
          newText?.cancel();
        }
      })
      .catch(() => {
        if (!renderSignal.aborted) setFailed(true);
      });
    return () => {
      lifetime.abort();
      newText?.cancel();
    };
  }, [pdf, page, requested, visible, zoom, signal, attempt, onWidth]);

  return (
    <div ref={host} className="kw-pdf-page" data-pdf-page={page}>
      {!failed && !ready ? <div className="kw-pdf-placeholder" aria-label={`第 ${page} 页加载中`} /> : null}
      {failed ? (
        <div className="kw-pdf-status" role="alert">
          <span>第 {page} 页没有显示出来</span>
          <Button
            color="secondary"
            variant="outline"
            size="sm"
            onClick={() => setAttempt((value) => value + 1)}
          >
            重试
          </Button>
        </div>
      ) : null}
      <div
        className="kw-pdf-surface kw-zoom-surface"
        data-zoom-surface
        style={{ '--document-zoom-width': `${width ?? 0}px` } as React.CSSProperties}
        hidden={!ready}
      >
        <canvas ref={canvas} className="kw-pdf-canvas" role="img" aria-label={`第 ${page} 页`} />
        <div ref={text} className="kw-pdf-text" />
      </div>
    </div>
  );
}
