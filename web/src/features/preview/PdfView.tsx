/**
 * PDF 阅读器（PPT、旧格式 Office 产物也先转成 PDF 再用它看），照 DSH 官方预览的 PDF 正文（client/pdf/pdf.js，MIT）：
 * 页面按需画（PdfPage）、可缩放（ZoomViewport）、文字能选中复制（pdfPage.ts 的文字层）。
 * 知识库的引用点开时带定位：滚到那一页并画出框（没有框时按原文找，见 pdfQuote.ts）。
 */
import { useCallback, useEffect, useState } from 'react';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { Skeleton } from '../../ui/Skeleton';
import type { PDFDocumentProxy } from 'pdfjs-dist';
import type { PdfLocate } from './locate';
import { findQuotes, marksOn, type QuoteHit } from './pdfQuote';
import { getDocument } from './pdfjs';
import { PdfPage } from './PdfPage';
import { ZoomViewport } from './ZoomViewport';
import { FIT_WIDTH, type ZoomPreference } from './zoom';

/** 页面四周留白（CSS 里 .kw-pdf-body 的 padding），适应宽度时左右扣掉。 */
const PAGE_GUTTER = 12;

type Load =
  | {
      readonly kind: 'loaded';
      readonly data: ArrayBuffer;
      readonly pdf: PDFDocumentProxy;
      readonly signal: AbortSignal;
      /** 第一页的高宽比：没画的页按它占位（量不出来时为空，用样式表的默认值） */
      readonly ratio?: number;
    }
  | { readonly kind: 'failed'; readonly data: ArrayBuffer };

/** 第一页的高宽比；取不到时为空（占位用样式表的默认值，不因此打不开文档）。 */
async function firstPageRatio(pdf: PDFDocumentProxy): Promise<number | undefined> {
  try {
    const viewport = (await pdf.getPage(1)).getViewport({ scale: 1 });
    return viewport.width > 0 ? viewport.height / viewport.width : undefined;
  } catch {
    return undefined;
  }
}

/** 要高亮的页与框：定位带框就用框；没有框时按每处原文在文字里找（找到之前先停在定位给的页）。 */
function useTarget(load: Load | undefined, focus?: PdfLocate): QuoteHit | undefined {
  const [found, setFound] = useState<{ readonly focus: PdfLocate; readonly hit: QuoteHit | undefined }>();
  const pdf = load?.kind === 'loaded' ? load.pdf : undefined;
  const boxed = focus && focus.boxes.length > 0;
  useEffect(() => {
    if (!pdf || !focus || boxed || focus.quotes.length === 0) return undefined;
    let live = true;
    findQuotes(pdf, focus.quotes, focus.page).then(
      (hit) => live && setFound({ focus, hit }),
      () => live && setFound({ focus, hit: undefined }),
    );
    return () => {
      live = false;
    };
  }, [pdf, focus, boxed]);
  if (!focus) return undefined;
  if (boxed) return { page: focus.boxes[0]!.page, boxes: focus.boxes };
  const hit = found?.focus === focus ? found.hit : undefined;
  return hit ?? (focus.page ? { page: focus.page, boxes: [] } : undefined);
}

export default function PdfView({ data, focus }: { readonly data: ArrayBuffer; readonly focus?: PdfLocate }) {
  const [load, setLoad] = useState<Load>();
  const [attempt, setAttempt] = useState(0);
  const [pageWidth, setPageWidth] = useState<number>();
  const [renderZoom, setRenderZoom] = useState(1);
  const [preference, setPreference] = useState<ZoomPreference>(FIT_WIDTH);
  const onPreference = useCallback((next: ZoomPreference) => setPreference(next), []);
  const target = useTarget(load, focus);

  useEffect(() => {
    const lifetime = new AbortController();
    // pdf.js 会转移 buffer 的所有权，传副本，组件重渲染时原数据还能用
    const task = getDocument({ data: new Uint8Array(data.slice(0)) });
    task.promise.then(
      async (pdf) => {
        const ratio = await firstPageRatio(pdf);
        if (!lifetime.signal.aborted) setLoad({ kind: 'loaded', data, pdf, signal: lifetime.signal, ratio });
      },
      () => {
        if (!lifetime.signal.aborted) setLoad({ kind: 'failed', data });
      },
    );
    return () => {
      lifetime.abort();
      void task.destroy();
    };
  }, [data, attempt]);

  if (load?.data !== data) {
    return (
      <div className="kw-pdf-state">
        <Skeleton rows={8} />
      </div>
    );
  }
  if (load.kind === 'failed') {
    return (
      <div className="kw-pdf-state">
        <div className="kw-pdf-status" role="alert">
          <span>这个 PDF 打不开，请下载后查看</span>
          <Button
            color="secondary"
            variant="outline"
            size="sm"
            onClick={() => setAttempt((value) => value + 1)}
          >
            重试
          </Button>
        </div>
      </div>
    );
  }
  return (
    <ZoomViewport
      preference={preference}
      pageWidth={pageWidth}
      inset={PAGE_GUTTER * 2}
      onPreference={onPreference}
      onRenderZoom={setRenderZoom}
    >
      <section className="kw-pdf-body" data-pdf-preview>
        {Array.from({ length: load.pdf.numPages }, (_, index) => (
          <PdfPage
            key={index}
            pdf={load.pdf}
            page={index + 1}
            requested={index === 0}
            signal={load.signal}
            zoom={renderZoom}
            onWidth={index === 0 ? setPageWidth : undefined}
            focused={target?.page === index + 1}
            marks={marksOn(target, index + 1)}
            ratio={load.ratio}
          />
        ))}
      </section>
    </ZoomViewport>
  );
}
