/** 引用上标：灰色小数字，点开看出处（标题、页码、摘录、查看原文）。 */
import { Popover } from '@openai/apps-sdk-ui/components/Popover';
import type { CitationSource } from '../events/types';

export type { CitationSource };

interface Props {
  readonly index: number;
  readonly source?: CitationSource;
}

function SourceContent({ index, source }: { readonly index: number; readonly source?: CitationSource }) {
  return (
    <div className="kw-citation-source">
      <div className="kw-citation-label">出处 {index}</div>
      {source ? (
        <>
          <div className="kw-citation-title">{source.title}</div>
          {source.page !== undefined ? <div className="kw-citation-page">第 {source.page} 页</div> : null}
          {source.excerpt ? <p className="kw-citation-excerpt">{source.excerpt}</p> : null}
          {source.url ? (
            <a className="kw-link" href={source.url} target="_blank" rel="noreferrer">
              查看原文
            </a>
          ) : null}
        </>
      ) : (
        <div className="kw-citation-page">出处原文暂不可用</div>
      )}
    </div>
  );
}

export function Citation({ index, source }: Props) {
  return (
    <Popover>
      <Popover.Trigger>
        <button type="button" className="kw-citation" aria-label={`查看出处 ${index}`}>
          {index}
        </button>
      </Popover.Trigger>
      <Popover.Content width={340} side="top" align="start">
        <SourceContent index={index} source={source} />
      </Popover.Content>
    </Popover>
  );
}
