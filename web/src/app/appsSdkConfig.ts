/** 组件库里 ButtonLink、TextLink 渲染链接时用 react-router 的 Link，站内跳转不整页刷新。 */
import type { Link } from 'react-router';

declare global {
  interface AppsSDKUIConfig {
    LinkComponent: typeof Link;
  }
}

export {};
