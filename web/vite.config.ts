import { build, loadEnv, type Plugin } from 'vite';
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

// 扩展包入口（导出 install(register)）的绝对路径；不设就是开源构建
const extensionEntry = (mode: string) => loadEnv(mode, '.', 'KNOWWORK_').KNOWWORK_WEB_EXTENSIONS || '';
// 扩展包的根：入口文件往上两级（<包>/src/index.ts）
const extensionRoot = (entry: string) => entry.replace(/[\\/][^\\/]+[\\/][^\\/]+$/, '');

// 开发服务器上也给 /embed/v1/widget.js（网页嵌入脚本）：每次请求按 vite.widget.config.ts 在内存里打一份，
// 本机就能把嵌入代码贴进别的页面试；线上由网关给构建好的文件（deploy/gateway/nginx.conf）
const widgetInDev = (): Plugin => ({
  name: 'knowwork-widget-dev',
  apply: 'serve',
  configureServer(server) {
    server.middlewares.use('/embed/v1/widget.js', (_request, response) => {
      build({ configFile: 'vite.widget.config.ts', logLevel: 'silent', build: { write: false } })
        .then((result) => {
          const outputs = Array.isArray(result) ? result : [result];
          const chunk = outputs.flatMap((item) => ('output' in item ? item.output : []))[0];
          response.setHeader('Content-Type', 'application/javascript; charset=utf-8');
          response.end(chunk && 'code' in chunk ? chunk.code : '');
        })
        .catch((error: unknown) => {
          response.statusCode = 500;
          response.end(String(error));
        });
    });
  },
});

// 前端只连业务服务：开发时把 /api（含 SSE 事件流）代理到本机业务服务。
export default defineConfig(({ mode }) => ({
  plugins: [react(), tailwindcss(), widgetInDev()],
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
      // 企业微信验证可信域名的根目录校验文件，同网关（deploy/gateway/nginx.conf）
      '^/WW_verify_[A-Za-z0-9]{1,64}\\.txt$': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        rewrite: (path) => `/api/auth/verify-files${path}`,
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
