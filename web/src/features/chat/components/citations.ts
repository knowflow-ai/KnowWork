import type { CitationSource } from '../events/types';
import { claimBefore } from './claim';

export const CITE_PREFIX = '#kw-cite-';

// 后端要求模型写 [n]；兼容 [ID:n]、[^n]。后面紧跟「(」是 Markdown 链接、紧跟「:」是脚注定义，都不算引用。
const MARK = /\[(?:ID:|\^)?(\d{1,4})\](?![(:])/g;

export const IMAGE_PREFIX = '#kw-img-';

// 回答里的图（服务端 knowledge/citations.py 的 IMAGE_MARK）：模型在它说明的那句话后面写 [IMG:n:j]
const IMAGE = /\[IMG:(\d{1,4}):(\d{1,3})\]/g;
// 模型偶尔把检索结果里的整段图片标记照抄进回答，归一成编号
const ECHOED = /<image\s+id=["']IMG:(\d{1,4}):(\d{1,3})["']\s*>[\s\S]*?<\/image>/gi;
// 流式输出到一半的编号先不显示，免得闪一下「[IMG:3」
const PARTIAL = /\[I(?:M(?:G(?::[\d:]*)?)?)?$/;

/** 把引用与图片标记改写成内部链接，再由 a 组件渲染成上标或图片。 */
export function markCitations(text: string): string {
  return text
    .replace(ECHOED, (_, n: string, j: string) => `[IMG:${n}:${j}]`)
    .replace(PARTIAL, '')
    .replace(IMAGE, (_, n: string, j: string) => `[图](${IMAGE_PREFIX}${n}-${j})`)
    .replace(MARK, (_, n: string, offset: number, whole: string) => {
      // 编号前面那句话放在 title 上：点开引用时，原样写了原文就按它把高亮收窄到那几处（claim.ts）
      const claim = claimBefore(whole, offset);
      return claim
        ? `[${n}](${CITE_PREFIX}${n} "${claim.replace(/["\\]/g, '\\$&')}")`
        : `[${n}](${CITE_PREFIX}${n})`;
    });
}

/** 复制回答用：去掉图片编号和照抄的图片标记（复制出去换不成图），图占的空行一起收掉。 */
export function withoutImages(text: string): string {
  return text
    .replace(ECHOED, '')
    .replace(IMAGE, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

/** 回答里引用到的编号，去重并保持首次出现的顺序。 */
export function citedNumbers(text: string): readonly number[] {
  return [...new Set([...text.matchAll(MARK)].map((match) => Number(match[1])))];
}

/** 回答引用到的一份文件：同一份文档的几处引用合成一项，文件按回答里首次引用的先后排。 */
export interface CitedFile {
  readonly key: string;
  readonly title: string;
  /** 引用这份文件的编号，从小到大 */
  readonly numbers: readonly number[];
  /** 引用到的页码，从小到大、去重 */
  readonly pages: readonly number[];
  /** 回答里首次引用的那处出处：点开文件时按它定位 */
  readonly first: CitationSource;
}

function fileKey(source: CitationSource): string {
  if (source.sourceId && source.docRef) return `doc:${source.sourceId}:${source.docRef}`;
  return source.url ? `url:${source.url}` : `title:${source.title}`;
}

const ascending = (values: readonly number[]) => [...new Set(values)].sort((a, b) => a - b);

/** 回答里引用到、引用目录里也有的编号，按文件归并。 */
export function citedFiles(
  text: string,
  sources: Readonly<Record<number, CitationSource>>,
): readonly CitedFile[] {
  const files = new Map<string, CitedFile>();
  for (const n of citedNumbers(text)) {
    const source = sources[n];
    if (!source) continue;
    const key = fileKey(source);
    const known = files.get(key) ?? { key, title: source.title, numbers: [], pages: [], first: source };
    files.set(key, {
      ...known,
      numbers: ascending([...known.numbers, n]),
      pages: source.page === undefined ? known.pages : ascending([...known.pages, source.page]),
    });
  }
  return [...files.values()];
}
