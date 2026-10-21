/** pdf.js 的加载：核心库、Worker，以及给官方查看器组件（pdfjs-dist/web/pdf_viewer.mjs）用的全局入口。 */
import * as pdfjsLib from 'pdfjs-dist';
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url';

pdfjsLib.GlobalWorkerOptions.workerSrc = workerUrl;
// 官方查看器组件一加载就从 globalThis.pdfjsLib 取核心库，所以要在导入它之前挂上（导入它的模块先导入本文件）
Reflect.set(globalThis, 'pdfjsLib', pdfjsLib);

export const { getDocument } = pdfjsLib;
