/**
 * 外壳的原生拖出（把文件拖到访达、桌面、别的应用）：页面被外壳（例如原生应用）加载时，外壳经 preload 在
 * window.knowworkShell.fileDrag 给出，合同照 DSH 官方 dsh-util-file-drag 的 DesktopFileDrag——页面交字节和文件名，外壳写成临时文件、
 * 起系统拖动，页面不碰本机路径。普通浏览器里没有（跨应用拖文件要靠外壳），这时文件只能下载。
 */

export interface BytesDragSource {
  readonly kind: 'bytes';
  readonly bytes: Uint8Array;
  readonly mediaType: string;
  readonly name?: string;
}

export interface ShellFileDrag {
  /** 准备一份要拖出去的文件，返回句柄（之后 start 或 release） */
  readonly prepare: (source: BytesDragSource, requestId: string) => Promise<string | undefined>;
  readonly cancel: (requestId: string) => void;
  /** 起系统拖动；系统接下了返回 true */
  readonly start: (handle: string) => Promise<boolean>;
  readonly release: (handle: string) => void;
}

export function shellFileDrag(): ShellFileDrag | null {
  const drag = (window as { knowworkShell?: { fileDrag?: Partial<ShellFileDrag> } }).knowworkShell?.fileDrag;
  if (
    typeof drag?.prepare !== 'function' ||
    typeof drag.cancel !== 'function' ||
    typeof drag.start !== 'function' ||
    typeof drag.release !== 'function'
  ) {
    return null;
  }
  return drag as ShellFileDrag;
}

// 外壳按类型给文件起名（官方规则：已知类型与后缀对不上时换成类型的后缀），所以要给对类型：通用的二进制类型会把 .pdf 改成 .bin。
// 认不出的给一个没人认识的类型，名字原样保留
const MEDIA_TYPES: Readonly<Record<string, string>> = {
  pdf: 'application/pdf',
  doc: 'application/msword',
  docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  xls: 'application/vnd.ms-excel',
  xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  ppt: 'application/vnd.ms-powerpoint',
  pptx: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
  csv: 'text/csv',
  txt: 'text/plain',
  md: 'text/markdown',
  json: 'application/json',
  html: 'text/html',
  png: 'image/png',
  jpg: 'image/jpeg',
  jpeg: 'image/jpeg',
  gif: 'image/gif',
  webp: 'image/webp',
  svg: 'image/svg+xml',
  zip: 'application/zip',
};
const UNKNOWN = 'application/x-knowwork-file';

export function mediaTypeOf(name: string): string {
  const dot = name.lastIndexOf('.');
  return dot < 0 ? UNKNOWN : (MEDIA_TYPES[name.slice(dot + 1).toLowerCase()] ?? UNKNOWN);
}
