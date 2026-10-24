/** pdf.js 的加载：核心库、Worker，以及给官方查看器组件（pdfjs-dist/legacy/web/pdf_viewer.mjs）用的全局入口。 */
// 用 legacy 构建：标准构建直接调 Math.sumPrecise 这类新 API，Chromium 145 及更早的浏览器没有，
// 字体解析在 Worker 里出错后退回替代字体，中文整页乱码（2026-10 录演示视频时撞上）。
// 企业里旧版 Chrome 和国产双核浏览器很常见，legacy 构建自带这些补丁，新浏览器上一样能用
import * as pdfjsLib from 'pdfjs-dist/legacy/build/pdf.mjs';
import workerUrl from 'pdfjs-dist/legacy/build/pdf.worker.min.mjs?url';

pdfjsLib.GlobalWorkerOptions.workerSrc = workerUrl;
// 官方查看器组件一加载就从 globalThis.pdfjsLib 取核心库，所以要在导入它之前挂上（导入它的模块先导入本文件）
Reflect.set(globalThis, 'pdfjsLib', pdfjsLib);

export const { getDocument } = pdfjsLib;
