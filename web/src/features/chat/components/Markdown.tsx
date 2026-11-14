/**
 * 回答正文渲染：用组件库的 Markdown（ChatGPT 的排版：段落、列表、标题、表格的间距，代码块带高亮与复制），
 * 加引用上标与知识库里的图。不渲染原始 HTML，防止注入。
 * sources 拉到之后，引用目录里没有的编号不再显示（不存在的编号去掉），由回答下方说明去掉了哪些。
 * 图（[IMG:n:j]）按第 n 条出处的第 j 张换成图片，点开在看图层里放大；出处还没拉到（本轮还在输出）或对不上时不显示。
 * mermaid 代码块画成图（MermaidDiagram）；正在输出、一直延伸到正文末尾的那块可能还没写完，先显示代码。
 * 指向文件的链接（判定照 DSH 官方，见 fileLinks.ts）点了交给 FileLinks 给的处理函数（会话页在右侧预览）；
 * 没给（云端对话、别的电脑上看本机会话）就只显示文字，不做成点了打不开的链接。
 */
import { createContext, useContext, useState, type ReactNode } from 'react';
import { CodeBlock } from '@openai/apps-sdk-ui/components/CodeBlock';
import { Markdown as MarkdownContent, useParseMarkdownPre } from '@openai/apps-sdk-ui/components/Markdown';
import { TextLink } from '@openai/apps-sdk-ui/components/TextLink';
import { ImageViewer } from '../../../ui/ImageViewer';
import { Citation, type CitationSource } from './Citation';
import { CITE_PREFIX, IMAGE_PREFIX, markCitations } from './citations';
import { keepDirectivesAsText } from './directives';
import { FileLinkContext, useFileOpener, type FileOpener } from './fileLinkContext';
import { fileTarget, type FileTarget } from './fileLinks';
import { MermaidDiagram } from './MermaidDiagram';

type Sources = Readonly<Record<number, CitationSource>>;

interface Props {
  readonly text: string;
  /** 本会话引用目录；undefined 表示还没拉到，此时编号照常显示。 */
  readonly sources?: Sources;
  /** 回答还在逐字输出：末尾那块代码可能还没写完，先不画图 */
  readonly streaming?: boolean;
}

/**
 * 出处经 context 交给链接组件，链接组件定义在模块里、引用不变。
 * 不能在渲染时现写 components：react-markdown 拿它当元素类型，每次渲染都是新函数，React 就把整段卸掉重建，
 * 回答过程中每来一次状态更新，图就重新加载、闪一下，点开的引用气泡也被重建（实测）。
 */
const SourcesContext = createContext<Sources | undefined>(undefined);

/** 正在输出时正文有多长：延伸到这里的代码块可能还没写完。不在输出时是 undefined。 */
const OpenEndContext = createContext<number | undefined>(undefined);

/** 给里面的回答正文、交付卡片接上这段对话里文件的打开方式；不给就是打不开（文件链接只显示文字）。 */
export function FileLinks({
  opener,
  children,
}: {
  readonly opener?: FileOpener;
  readonly children?: ReactNode;
}) {
  return <FileLinkContext.Provider value={opener}>{children}</FileLinkContext.Provider>;
}

/** 文件链接：是按钮不是链接（不离开页面，在右侧预览），照 DSH 官方悬停时虚线下划线；悬停提示写完整路径 */
function FileLink({ target, children }: { readonly target: FileTarget; readonly children?: ReactNode }) {
  const opener = useFileOpener();
  if (!opener) return <>{children}</>;
  return (
    <button type="button" className="kw-file-link" title={target.path} onClick={() => opener.open(target)}>
      {children}
    </button>
  );
}

/** 回答里的图：点开在看图层里放大，不跳新标签页。 */
function AnswerImage({
  url,
  alt,
  caption,
}: {
  readonly url: string;
  readonly alt: string;
  readonly caption?: string;
}) {
  const [zoomed, setZoomed] = useState(false);
  return (
    <>
      <button
        type="button"
        className="kw-answer-image"
        aria-label={`放大查看：${alt}`}
        onClick={() => setZoomed(true)}
      >
        <img src={url} alt={alt} loading="lazy" />
      </button>
      {zoomed ? <ImageViewer src={url} alt={alt} caption={caption} onClose={() => setZoomed(false)} /> : null}
    </>
  );
}

function Link({
  href,
  title,
  children,
}: {
  readonly href?: string;
  readonly title?: string;
  readonly children?: ReactNode;
}) {
  const sources = useContext(SourcesContext);
  if (href?.startsWith(IMAGE_PREFIX)) {
    const [n, j] = href.slice(IMAGE_PREFIX.length).split('-').map(Number);
    const image = sources?.[n ?? 0]?.images?.[j ?? 0];
    if (!image) return null;
    return <AnswerImage url={image.url} alt={image.caption || `出处 ${n} 里的图`} caption={image.caption} />;
  }
  if (href?.startsWith(CITE_PREFIX)) {
    const index = Number(href.slice(CITE_PREFIX.length));
    if (sources && !sources[index]) return null;
    // title 是回答里编号前面那句话（citations.ts 改写时放上的），点开时按它框出原文那一块
    return <Citation index={index} source={sources?.[index]} claim={title} />;
  }
  const file = href === undefined ? undefined : fileTarget(href);
  if (file) return <FileLink target={file}>{children}</FileLink>;
  return (
    <TextLink href={href ?? ''} forceExternal>
      {children}
    </TextLink>
  );
}

/** 组件库给自定义组件的 node 是宽松的对象：只取这块在原文里结束的位置 */
function endOffset(node: Readonly<Record<string, unknown>> | undefined): number {
  const position = node?.position as { readonly end?: { readonly offset?: number } } | undefined;
  return position?.end?.offset ?? 0;
}

/** 代码块：mermaid 画成图，其余用组件库的代码块（高亮、复制） */
function Pre({
  node,
  children,
  className,
}: {
  readonly node?: Readonly<Record<string, unknown>>;
  readonly children?: ReactNode;
  readonly className?: string;
}) {
  const openEnd = useContext(OpenEndContext);
  const { code, language } = useParseMarkdownPre(children);
  const unfinished = openEnd !== undefined && endOffset(node) >= openEnd;
  if (language === 'mermaid' && !unfinished) return <MermaidDiagram source={code} />;
  return (
    <CodeBlock language={language} className={className}>
      {code}
    </CodeBlock>
  );
}

/** 表格外面套一层，宽了在这层里横向滚动（组件库的表格靠折行放下，长数字会被折断） */
function Table({ children }: { readonly children?: ReactNode }) {
  return (
    <div className="kw-markdown-table">
      <table>{children}</table>
    </div>
  );
}

const COMPONENTS = { a: Link, pre: Pre, table: Table };
const REMARK_PLUGINS = [keepDirectivesAsText];

export function Markdown({ text, sources, streaming = false }: Props) {
  const marked = markCitations(text);
  return (
    <SourcesContext.Provider value={sources}>
      <OpenEndContext.Provider value={streaming ? marked.length : undefined}>
        <MarkdownContent className="kw-markdown" components={COMPONENTS} remarkPlugins={REMARK_PLUGINS}>
          {marked}
        </MarkdownContent>
      </OpenEndContext.Provider>
    </SourcesContext.Provider>
  );
}
