/**
 * 聊天组件在不在网页嵌入页里（服务端 channels/web.py）：嵌入页的访客不能带附件（附件按人存，公开访问的访客共用一个
 * 渠道账号），也不看运行详情（每一步的输入输出），服务端同样拦着。平常的网页端都打开。
 */
import { createContext, useContext } from 'react';

export interface EmbedMode {
  readonly attachments: boolean;
  readonly inspect: boolean;
}

export const NORMAL: EmbedMode = { attachments: true, inspect: true };
export const VISITOR: EmbedMode = { attachments: false, inspect: false };

export const EmbedModeContext = createContext<EmbedMode>(NORMAL);

export function useEmbedMode(): EmbedMode {
  return useContext(EmbedModeContext);
}
