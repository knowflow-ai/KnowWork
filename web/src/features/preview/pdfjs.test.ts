import { describe, expect, it } from 'vitest';
import * as pdfjsLib from 'pdfjs-dist/legacy/build/pdf.mjs';
import { WORKER_CACHE_KEY } from './pdfjs';

describe('pdf.js 的 worker 地址', () => {
  it('带上版本号，不复用浏览器里缓存的坏响应', () => {
    expect(pdfjsLib.GlobalWorkerOptions.workerSrc).toMatch(/pdf\.worker\.min.*\.mjs/);
    expect(pdfjsLib.GlobalWorkerOptions.workerSrc).toContain(WORKER_CACHE_KEY);
  });
});
