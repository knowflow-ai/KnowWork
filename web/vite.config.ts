import { loadEnv } from 'vite';
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

// 扩展包入口（导出 install(register)）的绝对路径；不设就是开源构建
const extensionEntry = (mode: string) => loadEnv(mode, '.', 'KNOWWORK_').KNOWWORK_WEB_EXTENSIONS || '';
// 扩展包的根：入口文件往上两级（<包>/src/index.ts）
const extensionRoot = (entry: string) => entry.replace(/[\\/][^\\/]+[\\/][^\\/]+$/, '');

// 前端只连业务服务：开发时把 /api（含 SSE 事件流）代理到本机业务服务。
export default defineConfig(({ mode }) => ({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      // 扩展的前端入口：开源构建用空实现（以 / 开头是相对项目根）；构建带扩展的镜像时换成扩展包的入口
      '@knowwork/extensions': extensionEntry(mode) || '/src/slots/none.ts',
      // 扩展包引用核心的组件、接口与工具时用的前缀
      '@knowwork/web': '/src',
    },
  },
  server: {
    // 扩展包在项目目录外面，开发服务器要放行它的文件（'.' 是项目根）
    fs: extensionEntry(mode) ? { allow: ['.', extensionRoot(extensionEntry(mode))] } : undefined,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    chunkSizeWarningLimit: 1500,
  },
  test: {
    environment: 'jsdom',
    globals: false,
    setupFiles: ['./src/test/setup.ts'],
    css: false,
    testTimeout: 20_000,
    // 组件库的 JS 直接引用 .module.css，Node 不认，交给 Vite 处理
    server: { deps: { inline: [/@openai\/apps-sdk-ui/] } },
    coverage: {
      provider: 'v8',
      include: ['src/**/*.{ts,tsx}'],
      exclude: ['src/main.tsx', 'src/test/**', 'src/**/*.test.{ts,tsx}', 'src/vite-env.d.ts'],
    },
  },
}));
