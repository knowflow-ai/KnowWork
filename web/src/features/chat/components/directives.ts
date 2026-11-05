/**
 * 组件库的 Markdown 固定开着「指令」语法（:name、::name、:::name，扩展自定义组件用）。回答里常有
 * 「key:value」「口径:company_count」这种写法，冒号后面的英文会被当成指令，渲染成一个空元素，字就丢了（实测）。
 * 我们不用指令：解析出来的指令一律按原文还原成文字，接在组件库自带的插件之后跑。
 */

interface Point {
  readonly line: number;
  readonly column: number;
  readonly offset?: number;
}

interface MdNode {
  readonly type: string;
  readonly children?: readonly MdNode[];
  readonly value?: string;
  readonly position?: { readonly start: Point; readonly end: Point };
}

const INLINE = 'textDirective';
const BLOCKS = new Set(['leafDirective', 'containerDirective']);

function original(node: MdNode, source: string): string {
  const start = node.position?.start.offset;
  const end = node.position?.end.offset;
  return start === undefined || end === undefined ? '' : source.slice(start, end);
}

function restore(node: MdNode, source: string): MdNode {
  if (!node.children) return node;
  return {
    ...node,
    children: node.children.map((child) => {
      const text = { type: 'text', value: original(child, source), position: child.position };
      if (child.type === INLINE) return text;
      if (BLOCKS.has(child.type)) return { type: 'paragraph', children: [text], position: child.position };
      return restore(child, source);
    }),
  };
}

/** remark 插件：把指令节点换回原文 */
export function keepDirectivesAsText() {
  return (tree: MdNode, file: { readonly value: unknown }) => restore(tree, String(file.value));
}
