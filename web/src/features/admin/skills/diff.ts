/**
 * 审核技能提交时的对比：SKILL.md 正文逐行对比（最长公共子序列），技能包文件按路径与摘要比出增删改。
 * 正文一般几百行，O(n·m) 足够；太长（超过 MAX_CELLS）时不算对比，整段按新增显示，免得卡住页面。
 */
import type { SkillFile } from '../../../api/skills';

export type LineKind = 'same' | 'add' | 'del';

export interface DiffLine {
  readonly kind: LineKind;
  readonly text: string;
}

const MAX_CELLS = 4_000_000;

const linesOf = (text: string): string[] => (text === '' ? [] : text.replace(/\r\n/g, '\n').split('\n'));

export function lineDiff(before: string, after: string): readonly DiffLine[] {
  const a = linesOf(before);
  const b = linesOf(after);
  if (a.length * b.length > MAX_CELLS) return b.map((text) => ({ kind: 'add', text }));
  // lcs[i][j]：a[i..] 与 b[j..] 的最长公共子序列长度，按行存成一块，(i, j) 在 i * width + j
  const width = b.length + 1;
  const lcs = new Uint32Array((a.length + 1) * width);
  const at = (i: number, j: number): number => lcs[i * width + j] ?? 0;
  for (let i = a.length - 1; i >= 0; i -= 1) {
    for (let j = b.length - 1; j >= 0; j -= 1) {
      lcs[i * width + j] = a[i] === b[j] ? at(i + 1, j + 1) + 1 : Math.max(at(i + 1, j), at(i, j + 1));
    }
  }
  const out: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    const left = a[i] ?? '';
    const right = b[j] ?? '';
    if (left === right) {
      out.push({ kind: 'same', text: left });
      i += 1;
      j += 1;
    } else if (at(i + 1, j) >= at(i, j + 1)) {
      out.push({ kind: 'del', text: left });
      i += 1;
    } else {
      out.push({ kind: 'add', text: right });
      j += 1;
    }
  }
  return [
    ...out,
    ...a.slice(i).map((text) => ({ kind: 'del' as const, text })),
    ...b.slice(j).map((text) => ({ kind: 'add' as const, text })),
  ];
}

/** 有没有改动（全是 same 就是没改）。 */
export const hasChanges = (lines: readonly DiffLine[]): boolean => lines.some((line) => line.kind !== 'same');

export type FileChange = 'added' | 'removed' | 'changed' | 'same';

export interface FileRow {
  readonly path: string;
  readonly size: number;
  readonly change: FileChange;
}

/** 文件清单的增删改；before 为空（第一次进空间）时全部算新增。按路径排序。 */
export function fileChanges(
  before: readonly SkillFile[] | null,
  after: readonly SkillFile[],
): readonly FileRow[] {
  const old = new Map((before ?? []).map((file) => [file.path, file]));
  const rows: FileRow[] = after.map((file) => {
    const prev = old.get(file.path);
    const change: FileChange =
      before === null || !prev ? 'added' : prev.sha256 === file.sha256 ? 'same' : 'changed';
    return { path: file.path, size: file.size, change };
  });
  const kept = new Set(after.map((file) => file.path));
  const removed = (before ?? [])
    .filter((file) => !kept.has(file.path))
    .map((file) => ({ path: file.path, size: file.size, change: 'removed' as const }));
  return [...rows, ...removed].sort((x, y) => x.path.localeCompare(y.path));
}
