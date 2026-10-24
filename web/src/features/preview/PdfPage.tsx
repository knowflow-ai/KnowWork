/**
 * PDF 的一页，照 DSH 官方预览（client/pdf/pdf.js 的 PdfPage，MIT）：
 * - 滚到视口上下各一屏的范围内才画（IntersectionObserver），没画之前是按页面比例留高的灰色占位；
 * - 缩放停下后，只重画还在视口附近的页面；先画到离屏 canvas 再一次拷过来，重画期间旧画面不闪；
 * - 文字层只在第一次画时建，之后随画面宽度缩放；
 * - 引用定位到这一页时马上画、滚过来，框按页面比例叠在画面上（随缩放一起变）；高亮区的顶端对齐视口顶部（留一点边距），
 *   从头往下看。原来把第一个框居中，整块高亮时上半屏是上一页的空白，下半截又被挤出视口。
 */
import { useEffect, useRef, useState } from 'react';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import type { PDFDocumentProxy } from 'pdfjs-dist';
import type { Box } from './locate';
import { topmost } from './pdfQuote';
import { pdfTextRenderer, renderPdfPage, type RenderTask } from './pdfRender';

export function PdfPage({
  pdf,
  page,
  requested: initiallyRequested,
  signal,
  zoom,
  onWidth,
  focused = false,
  marks,
  ratio,
}: {
  readonly pdf: PDFDocumentProxy;
  readonly page: number;
  /** 一开始就画（第一页）。 */
  readonly requested: boolean;
  readonly signal: AbortSignal;
  readonly zoom: number;
  /** 量出 100% 时的页宽后告诉外层，给适应宽度用（只有第一页给）。 */
  readonly onWidth?: (width: number) => void;
  /** 引用定位到这一页：马上画、滚过来 */
  readonly focused?: boolean;
  /** 这一页上要高亮的框（占页面宽高的比例） */
  readonly marks?: readonly Box[];
  /**
   * 页面的高宽比（PdfView 打开时量第一页，照 WeKnora 的 PdfSourceViewer）：没画的页按它占位，画出来时高度不变。
   * 原来一律按竖版 3:4 占位，横版幻灯片画出来缩掉一大截，刚滚到的引用位置被顶偏。不给时用样式表的默认值（A4）
   */
  readonly ratio?: number;
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

  // 定位到这一页：先把占位滚过来（滚进视口就会画），画好以后再把最靠上的框滚到视口顶部（边距见 .kw-pdf-mark）
  const topMark = useRef<HTMLDivElement>(null);
  const top = marks ? topmost(marks) : -1;
  useEffect(() => {
    if (!focused) return;
    (ready && topMark.current ? topMark.current : host.current)?.scrollIntoView?.({ block: 'start' });
  }, [focused, ready, marks]);

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
      {!failed && !ready ? (
        <div
          className="kw-pdf-placeholder"
          aria-label={`第 ${page} 页加载中`}
          style={ratio ? { aspectRatio: `1 / ${ratio}` } : undefined}
        />
      ) : null}
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
        {(marks ?? []).map((box, index) => (
          <div
            key={`${box.x0}-${box.y0}-${index}`}
            ref={index === top ? topMark : undefined}
            className="kw-pdf-mark"
            data-pdf-mark
            style={{
              left: `${box.x0 * 100}%`,
              top: `${box.y0 * 100}%`,
              width: `${(box.x1 - box.x0) * 100}%`,
              height: `${(box.y1 - box.y0) * 100}%`,
            }}
          />
        ))}
        <div ref={text} className="kw-pdf-text" />
      </div>
    </div>
  );
}
