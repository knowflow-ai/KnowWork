/**
 * 网页嵌入脚本单独打包（src/embed/widget.ts）：接入方页面用 <script> 引入的一个文件，地址固定（/embed/v1/widget.js），
 * 立即执行、不依赖 React。在主应用之后构建，输出到 dist 里，不清空主应用的产物。
 */
import { defineConfig } from 'vite';

export default defineConfig({
  publicDir: false,
  build: {
    outDir: 'dist/embed/v1',
    emptyOutDir: false,
    sourcemap: false,
    lib: {
      entry: 'src/embed/widget.ts',
      formats: ['iife'],
      name: 'KnowWorkWidget',
      fileName: () => 'widget.js',
    },
  },
});
