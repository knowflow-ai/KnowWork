/**
 * 回答正文渲染：Markdown 加引用上标与知识库里的图。不渲染原始 HTML，防止注入。
 * sources 拉到之后，引用目录里没有的编号不再显示（不存在的编号去掉），由回答下方说明去掉了哪些。
 * 图（[IMG:n:j]）按第 n 条出处的第 j 张换成图片，点开在看图层里放大；出处还没拉到（本轮还在输出）或对不上时不显示。
 * mermaid 代码块画成图（MermaidDiagram）；正在输出、一直延伸到正文末尾的那块可能还没写完，先显示代码。
 */
import {
  createContext,
  isValidElement,
  useContext,
  useState,
  type ComponentProps,
  type ReactNode,
} from 'react';
import ReactMarkdown, { type Components, type ExtraProps } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { ImageViewer } from '../../../ui/ImageViewer';
import { Citation, type CitationSource } from './Citation';
import { CITE_PREFIX, IMAGE_PREFIX, markCitations } from './citations';
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
  return (
    <a href={href} target="_blank" rel="noreferrer">
      {children}
    </a>
  );
}

/** 代码块是 mermaid 时取出原文，否则 undefined。 */
function mermaidSource(children: ReactNode): string | undefined {
  if (!isValidElement<{ className?: string; children?: ReactNode }>(children)) return undefined;
  const { className, children: code } = children.props;
  if (!/\blanguage-mermaid\b/.test(className ?? '') || typeof code !== 'string') return undefined;
  return code.replace(/\n$/, '');
}

function Pre({ node, children, ...rest }: ComponentProps<'pre'> & ExtraProps) {
  const openEnd = useContext(OpenEndContext);
  const source = mermaidSource(children);
  const unfinished = openEnd !== undefined && (node?.position?.end.offset ?? 0) >= openEnd;
  if (source === undefined || unfinished) return <pre {...rest}>{children}</pre>;
  return <MermaidDiagram source={source} />;
}

const COMPONENTS: Components = { a: Link, pre: Pre };

export function Markdown({ text, sources, streaming = false }: Props) {
  const marked = markCitations(text);
  return (
    <SourcesContext.Provider value={sources}>
      <OpenEndContext.Provider value={streaming ? marked.length : undefined}>
        <div className="kw-markdown">
          <ReactMarkdown remarkPlugins={[remarkGfm]} components={COMPONENTS}>
            {marked}
          </ReactMarkdown>
        </div>
      </OpenEndContext.Provider>
    </SourcesContext.Provider>
  );
}
