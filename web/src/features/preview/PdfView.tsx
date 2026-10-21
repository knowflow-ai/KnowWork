/**
 * PDF 阅读器（Word、PPT 也先转成 PDF 再用它看），照 DSH 官方预览的 PDF 正文（client/pdf/pdf.js，MIT）：
 * 页面按需画（PdfPage）、可缩放（ZoomViewport）、文字能选中复制（pdfPage.ts 的文字层）。
 */
import { useCallback, useEffect, useState } from 'react';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { Skeleton } from '../../ui/Skeleton';
import type { PDFDocumentProxy } from 'pdfjs-dist';
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
    }
  | { readonly kind: 'failed'; readonly data: ArrayBuffer };

export default function PdfView({ data }: { readonly data: ArrayBuffer }) {
  const [load, setLoad] = useState<Load>();
  const [attempt, setAttempt] = useState(0);
  const [pageWidth, setPageWidth] = useState<number>();
  const [renderZoom, setRenderZoom] = useState(1);
  const [preference, setPreference] = useState<ZoomPreference>(FIT_WIDTH);
  const onPreference = useCallback((next: ZoomPreference) => setPreference(next), []);

  useEffect(() => {
    const lifetime = new AbortController();
    // pdf.js 会转移 buffer 的所有权，传副本，组件重渲染时原数据还能用
    const task = getDocument({ data: new Uint8Array(data.slice(0)) });
    task.promise.then(
      (pdf) => {
        if (!lifetime.signal.aborted) setLoad({ kind: 'loaded', data, pdf, signal: lifetime.signal });
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
          />
        ))}
      </section>
    </ZoomViewport>
  );
}
