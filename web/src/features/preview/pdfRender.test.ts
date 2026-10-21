import { afterEach, describe, expect, it, vi } from 'vitest';
import type { PDFDocumentProxy } from 'pdfjs-dist';

// jsdom 没有 canvas 绘图，pdf.js 换成替身：只看画页时怎么定尺寸、怎么挂文字层、怎么取消
vi.mock('./pdfjs', () => ({}));
const built = vi.hoisted(() => ({ layers: [] as { cancelled: boolean; options: unknown }[] }));
vi.mock('pdfjs-dist/web/pdf_viewer.mjs', () => ({
  TextLayerBuilder: class {
    div = document.createElement('div');
    record = { cancelled: false, options: undefined as unknown };

    constructor() {
      built.layers.push(this.record);
    }

    render(options: unknown) {
      this.record.options = options;
      return Promise.resolve();
    }

    cancel() {
      this.record.cancelled = true;
    }
  },
}));

const { PAGE_SCALE, pdfTextRenderer, renderPdfPage } = await import('./pdfRender');

function fakePdf(width = 612, height = 792) {
  const calls = { renders: [] as Record<string, unknown>[], cleaned: 0, cancelled: 0 };
  const page = {
    getViewport: ({ scale }: { scale: number }) => ({
      width: width * scale,
      height: height * scale,
      scale,
      userUnit: 1,
    }),
    render: (options: Record<string, unknown>) => {
      calls.renders.push(options);
      return { promise: Promise.resolve(), cancel: () => (calls.cancelled += 1) };
    },
    cleanup: () => (calls.cleaned += 1),
  };
  const pdf = { getPage: () => Promise.resolve(page) } as unknown as PDFDocumentProxy;
  return { pdf, calls };
}

afterEach(() => {
  built.layers = [];
});

describe('renderPdfPage', () => {
  it('100% 按 96 DPI 算，位图按像素比放大，返回 100% 时的页面大小并释放页面', async () => {
    const { pdf, calls } = fakePdf();
    const canvas = document.createElement('canvas');
    const size = await renderPdfPage(pdf, 1, canvas, new AbortController().signal, 2);
    expect(size).toEqual({ width: 612 * PAGE_SCALE, height: 792 * PAGE_SCALE });
    expect([canvas.width, canvas.height]).toEqual([1632, 2112]);
    expect(calls.renders[0]?.transform).toEqual([2, 0, 0, 2, 0, 0]);
    expect(calls.cleaned).toBe(1);
  });

  it('超大页面限制位图像素，不让浏览器分配过大的画布', async () => {
    const { pdf } = fakePdf(6000, 6000);
    const canvas = document.createElement('canvas');
    await renderPdfPage(pdf, 1, canvas, new AbortController().signal, 3);
    expect(canvas.width * canvas.height).toBeLessThanOrEqual(16_777_216);
  });

  it('已取消的请求不画', async () => {
    const { pdf, calls } = fakePdf();
    const controller = new AbortController();
    controller.abort();
    await expect(
      renderPdfPage(pdf, 1, document.createElement('canvas'), controller.signal, 1),
    ).rejects.toThrow();
    expect(calls.renders).toEqual([]);
  });
});

describe('pdfTextRenderer', () => {
  it('文字层挂在画面上方，带上 pdf.js 排版要的缩放变量；取消时移除', async () => {
    const host = document.createElement('div');
    const { pdf } = fakePdf();
    const page = await pdf.getPage(1);
    const viewport = page.getViewport({ scale: PAGE_SCALE });
    const task = pdfTextRenderer(host)(page, viewport);
    await task.promise;
    const layer = host.firstElementChild as HTMLElement;
    expect(layer.style.getPropertyValue('--total-scale-factor')).toBe(String(PAGE_SCALE));
    expect(layer.style.getPropertyValue('--scale-round-x')).toBe('1px');
    expect(built.layers[0]?.options).toEqual({ viewport });
    task.cancel();
    expect(built.layers[0]?.cancelled).toBe(true);
    expect(host.childElementCount).toBe(0);
  });
});
