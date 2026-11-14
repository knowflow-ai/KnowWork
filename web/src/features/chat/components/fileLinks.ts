/**
 * 回答里的链接是不是文件（照 DSH 官方聊天界面的判定抄的：@deepseek-ai/dsh-web-frontend 0.2.2-rc.1 里
 * dsh-client-ui-primitives 的 Markdown 链接分类）：
 * - 去掉 # 之后带 ? 的不是文件；按网址编码解开，空的、带控制字符的不是文件；
 * - 以 /、\、./、../、盘符开头的是文件；其余能当网址解析的（https:、mailto: 等）不是文件，解析不了的（report.md、out/a.csv）是文件；
 * - 末尾的 :行、:行:列、:行-行，或 #L行-L行 是行号。
 * 相对路径怎么解析由打开它的一方定（官方按这段对话的工作目录）。
 */
export interface FileTarget {
  readonly path: string;
  /** 从 1 起算 */
  readonly line?: number;
}

/** 看起来就是路径：以 /、\、./、../ 或盘符开头 */
function pathLike(value: string): boolean {
  return /^(?:[\\/]|\.{1,2}[\\/]|[a-z]:[\\/])/i.test(value);
}

function parsesAsUrl(value: string): boolean {
  try {
    new URL(value);
    return true;
  } catch {
    return false;
  }
}

export function fileTarget(href: string): FileTarget | undefined {
  const hash = href.indexOf('#');
  const raw = hash < 0 ? href : href.slice(0, hash);
  if (raw.includes('?')) return undefined;
  let path: string;
  try {
    path = decodeURIComponent(raw);
  } catch {
    return undefined;
  }
  // eslint-disable-next-line no-control-regex -- 就是要拦控制字符
  if (path.length === 0 || /[\u0000-\u001f\u007f]/.test(path)) return undefined;
  if (!pathLike(path) && parsesAsUrl(path)) return undefined;
  const suffix = hash < 0 ? /:(\d+)(?::\d+|-(\d+))?$/.exec(raw) : null;
  if (suffix !== null) path = path.slice(0, -suffix[0].length);
  if (path.length === 0) return undefined;
  const range = suffix ?? (hash < 0 ? null : /^L(\d+)(?:-L(\d+))?$/.exec(href.slice(hash + 1)));
  if (range === null) return { path };
  const start = Number(range[1]);
  const end = range[2] === undefined ? start : Number(range[2]);
  if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start < 1 || end < start) return { path };
  return { path, line: start };
}

/** 路径最后一段（文件名）：/ 与 \ 都算分隔符 */
export function fileNameOf(path: string): string {
  return path.split(/[\\/]/u).filter(Boolean).pop() ?? path;
}
