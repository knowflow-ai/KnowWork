/**
 * 这段对话里的文件点了交给谁（回答里的文件链接、交付卡片、消息上的附件卡片共用，会话页按对话的来源给，见 hooks/useFileLinks）：
 * open 在右侧预览，save 存到本机（网页是下载，桌面端本机会话是另存为），read 给拖出去用。不给就是这里打不开，只显示文字。
 */
import { createContext, useContext } from 'react';
import type { AttachmentSource } from '../sources';
import type { FileTarget } from './fileLinks';

export interface FileOpener {
  readonly open: (target: FileTarget) => void;
  /**
   * 附件（只读）：能编辑的格式按它的同名副本在编辑器里打开，别的只读预览原件；不给时消息上的附件卡片点不开。
   * 消息上的给工作区路径（input/...）；输入框里还没发出去的给 id（会话页经输入框的 onOpenDraft 调，只有能编辑的格式会这样打开）
   */
  readonly openAttachment?: (source: AttachmentSource) => void;
  readonly save?: (path: string) => void;
  /** 原件字节（拖到别的应用时交给外壳，见 useFileDrag） */
  readonly read?: (path: string) => Promise<ArrayBuffer>;
}

export const FileLinkContext = createContext<FileOpener | undefined>(undefined);

export function useFileOpener(): FileOpener | undefined {
  return useContext(FileLinkContext);
}
