/**
 * 回答正文里用反引号点到的文件变成可点的链接（照 DSH 官方交付插件 dsh-client-ui-deliverables：把收尾正文里匹配的
 * 行内代码引用转成链接，在右侧打开）。和官方一样，认哪些文件不看正文，看这一轮记下的：成功写入、修改的文件
 * （官方 write、edit 工具的 file_path）和显式交付的文件（present）。正文里写的只是拿来对的。
 * - 行内代码要像一个路径（没有空白，带扩展名或斜杠），整段就是它，命令、公式这类不认；
 * - 对得上：和记下的路径一样，或者是它结尾的一段（写了文件名或相对路径）；对上两个以上（同名文件在不同目录）就不认，
 *   宁可不链，不链错。
 */
import type { DeliverableView, StepView } from '../events/types';

/** 改文件的官方工具：成功了就算这一轮改过的文件 */
const WRITES = new Set(['write', 'edit']);
// 会话工作目录（/sessions/<会话 id>）是内部路径，对的时候去掉
const SESSION_ROOT = /^\/sessions\/[^/]+\//;
const PATH_LIKE = /^[^\s`]+$/;

function normalized(path: string): string {
  return path.replace(SESSION_ROOT, '').replace(/^\.\//, '');
}

function filePathOf(step: StepView): string | undefined {
  if (!WRITES.has(step.name) || step.status !== 'done') return undefined;
  try {
    const args = JSON.parse(step.arguments) as { readonly file_path?: unknown };
    return typeof args.file_path === 'string' && args.file_path.length > 0 ? args.file_path : undefined;
  } catch {
    return undefined;
  }
}

/** 这一轮记下的文件：改过的加交付的，去重，保持出现的先后 */
export function turnFiles(
  steps: readonly StepView[],
  deliverables: readonly DeliverableView[] = [],
): string[] {
  const paths = [...steps.map(filePathOf), ...deliverables.map((file) => file.path)];
  return [...new Set(paths.filter((path): path is string => path !== undefined))];
}

/** 正文里这段行内代码点的是不是这一轮记下的某个文件：是就交回记下的原样路径（打开时用），对不上或对上不止一个是 undefined */
export function turnFileOf(code: string, files: readonly string[]): string | undefined {
  const text = normalized(code.trim());
  if (text.length === 0 || !PATH_LIKE.test(text) || !/[./]/.test(text)) return undefined;
  const matches = files.filter((file) => {
    const path = normalized(file);
    return path === text || path.endsWith(`/${text}`);
  });
  return matches.length === 1 ? matches[0] : undefined;
}
