/**
 * 各类文件的显示样式（名称、色块、图标），产物卡片与附件卡片共用。
 * 单独成文件：和组件放在一起导出会让开发时的热更新失效（react-refresh/only-export-components）。
 */
import {
  File,
  FileBlank,
  FileDocument,
  FilePresentation,
  FileSpreadsheet,
} from '@openai/apps-sdk-ui/components/Icon';
import type { ReactNode } from 'react';
import type { FileKindKey } from './fileKind';

interface FileKind {
  readonly label: string;
  readonly color: string;
  readonly icon: ReactNode;
}

/** 文件类型色块：彩色只出现在状态、链接与文件类型图标上（界面规范第 1 节），色值是产品自己的。 */
export const FILE_KINDS: Record<FileKindKey, FileKind> = {
  doc: { label: 'Word 文档', color: '#2b7de9', icon: <FileDocument /> },
  sheet: { label: 'Excel 表格', color: '#52c41a', icon: <FileSpreadsheet /> },
  slide: { label: 'PPT 演示文稿', color: '#fa8c16', icon: <FilePresentation /> },
  pdf: { label: 'PDF 文件', color: '#ff4d4f', icon: <File /> },
  other: { label: '文件', color: '#8c8c8c', icon: <FileBlank /> },
};
