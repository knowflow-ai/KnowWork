/** pdf.js 的加载：核心库、Worker，以及给官方查看器组件（pdfjs-dist/legacy/web/pdf_viewer.mjs）用的全局入口。 */
// 用 legacy 构建：标准构建直接调 Math.sumPrecise 这类新 API，Chromium 145 及更早的浏览器没有，
// 字体解析在 Worker 里出错后退回替代字体，中文整页乱码（2026-10 录演示视频时撞上）。
// 企业里旧版 Chrome 和国产双核浏览器很常见，legacy 构建自带这些补丁，新浏览器上一样能用
import * as pdfjsLib from 'pdfjs-dist/legacy/build/pdf.mjs';
import workerUrl from 'pdfjs-dist/legacy/build/pdf.worker.min.mjs?url';

// 地址后面加版本号，换一个缓存键：v0.0.3 之前网关按 application/octet-stream 返回 .mjs，浏览器不认它是模块脚本，
// 却按 /assets/ 的规则把这份坏响应 immutable 缓存了一年。worker 内容没变，文件名里的哈希也不变，
// 打开过 PDF 预览的浏览器网关修好后照样拿缓存里的坏响应。升级 pdfjs-dist 后哈希会变，这个参数就可以去掉了
export const WORKER_CACHE_KEY = 'v=2';

pdfjsLib.GlobalWorkerOptions.workerSrc = `${workerUrl}${workerUrl.includes('?') ? '&' : '?'}${WORKER_CACHE_KEY}`;
// 官方查看器组件一加载就从 globalThis.pdfjsLib 取核心库，所以要在导入它之前挂上（导入它的模块先导入本文件）
Reflect.set(globalThis, 'pdfjsLib', pdfjsLib);

export const { getDocument } = pdfjsLib;
