/**
 * 文件面板目录树的纯函数（照 DSH 官方 dsh-client-ui-sidebar-files 的目录树）：一层里文件夹在前、再按名字的自然顺序排、
 * 不分大小写；子项路径是这一层的路径和名字用 / 拼起来（根是空串）；点开头的照样列出，只是字淡一些。
 */
import type { DirectoryEntry } from '../sources';

// 固定按英文规则比较：哪台电脑上排出来都一样（西文在前、中文在后），数字按大小排（第2章在第10章前）
const collator = new Intl.Collator('en', { numeric: true, sensitivity: 'base' });

export function sortEntries(entries: readonly DirectoryEntry[]): readonly DirectoryEntry[] {
  return [...entries].sort((a, b) => {
    const folders = Number(b.type === 'directory') - Number(a.type === 'directory');
    return folders !== 0 ? folders : collator.compare(a.name, b.name);
  });
}

export function childPath(parent: string, name: string): string {
  return parent ? `${parent}/${name}` : name;
}

export function isHidden(name: string): boolean {
  return name.startsWith('.');
}

/**
 * 文件路径换成相对工作文件夹、用 / 分隔的写法（回答里的链接可能是 ./a、a\\b、工作文件夹下的绝对路径）；不在工作文件夹里
 * 的返回 null
 */
export function workspacePathOf(path: string, root: string | null): string | null {
  const slashed = path.replace(/\\/g, '/');
  const base = root?.replace(/\\/g, '/').replace(/\/+$/, '');
  if (base && (slashed === base || slashed.startsWith(`${base}/`))) return slashed.slice(base.length + 1);
  if (slashed.startsWith('/') || /^[A-Za-z]:\//.test(slashed)) return null;
  const parts = slashed.split('/').filter((part) => part !== '' && part !== '.');
  return parts.includes('..') ? null : parts.join('/');
}

/** 一个文件的各级上级文件夹（不含它自己）：a/b/c.md → a、a/b */
export function ancestorsOf(path: string): readonly string[] {
  const parts = path.split('/');
  return parts.slice(0, -1).map((_, index) => parts.slice(0, index + 1).join('/'));
}
