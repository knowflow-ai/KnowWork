/**
 * 拿原文比位置时的归一：只留字母和数字（含中文），英文小写。列表编号后的点、表格分隔符、标点与空白在 PDF 文字、
 * Word 渲染结果、分块正文和回答里常对不上，比的时候都不看（服务端对 Word 段落时同样只比字母和数字）。
 */
export function loose(text: string): string {
  return text.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, '');
}
