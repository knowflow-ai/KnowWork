export type FileKindKey = 'doc' | 'sheet' | 'slide' | 'pdf' | 'image' | 'web' | 'other';

/** 按扩展名判断文件类型，决定卡片图标与色块。 */
export function fileKindOf(name: string): FileKindKey {
  const ext = name.split('.').pop()?.toLowerCase() ?? '';
  if (['doc', 'docx', 'md', 'txt'].includes(ext)) return 'doc';
  if (['xls', 'xlsx', 'csv'].includes(ext)) return 'sheet';
  if (['ppt', 'pptx'].includes(ext)) return 'slide';
  if (ext === 'pdf') return 'pdf';
  if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg'].includes(ext)) return 'image';
  if (['html', 'htm'].includes(ext)) return 'web';
  return 'other';
}
