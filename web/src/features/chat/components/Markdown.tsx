/**
 * 回答正文渲染：Markdown 加引用上标。不渲染原始 HTML，防止注入。
 * sources 拉到之后，引用目录里没有的编号不再显示（技术方案 8.3：不存在的编号去掉），由回答下方说明去掉了哪些。
 */
import ReactMarkdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Citation, type CitationSource } from './Citation';
import { CITE_PREFIX, markCitations } from './citations';

interface Props {
  readonly text: string;
  /** 本会话引用目录；undefined 表示还没拉到，此时编号照常显示。 */
  readonly sources?: Readonly<Record<number, CitationSource>>;
}

export function Markdown({ text, sources }: Props) {
  const components: Components = {
    a: ({ href, children }) => {
      if (href?.startsWith(CITE_PREFIX)) {
        const index = Number(href.slice(CITE_PREFIX.length));
        if (sources && !sources[index]) return null;
        return <Citation index={index} source={sources?.[index]} />;
      }
      return (
        <a href={href} target="_blank" rel="noreferrer">
          {children}
        </a>
      );
    },
  };
  return (
    <div className="kw-markdown">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {markCitations(text)}
      </ReactMarkdown>
    </div>
  );
}
