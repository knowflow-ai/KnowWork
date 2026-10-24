/**
 * 引用上标：灰色小数字，点开看出处（标题、页码、摘录、查看原文）。
 * 知识源注册了引用预览器（插槽 citation.preview）时，「查看原文」在预览抽屉里打开原文并按定位高亮；
 * 预览文件点开时才去取，没有权限或文档没了就提示一句。其余知识源跳它给的原文链接。
 */
import { useState } from 'react';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { Popover } from '@openai/apps-sdk-ui/components/Popover';
import { FilePreview } from '../../preview/FilePreview';
import type { CitationSource } from '../events/types';
import { focusOn } from './claim';
import { previewerOf, useSourcePreview } from './useSourcePreview';

export type { CitationSource };

interface Props {
  readonly index: number;
  readonly source?: CitationSource;
  /** 回答里这个编号前面那句话：原样写了原文时按它把高亮收窄到那几处（claim.ts），否则整块高亮 */
  readonly claim?: string;
}

function SourceContent({
  index,
  source,
  onPreview,
  opening,
}: {
  readonly index: number;
  readonly source?: CitationSource;
  readonly onPreview?: () => void;
  readonly opening: boolean;
}) {
  return (
    <div className="kw-citation-source">
      <div className="kw-citation-label">出处 {index}</div>
      {source ? (
        <>
          <div className="kw-citation-title">{source.title}</div>
          {source.page !== undefined ? <div className="kw-citation-page">第 {source.page} 页</div> : null}
          {source.excerpt ? <p className="kw-citation-excerpt">{source.excerpt}</p> : null}
          {onPreview ? (
            <Button color="secondary" variant="outline" size="sm" loading={opening} onClick={onPreview}>
              查看原文
            </Button>
          ) : source.url ? (
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

export function Citation({ index, source: whole, claim }: Props) {
  const source = focusOn(whole, claim);
  // 气泡受控：打开原文时先收起，不然它浮在预览抽屉上面挡着原文
  const [shown, setShown] = useState(false);
  const { opened, opening, open: openSource, close } = useSourcePreview();
  const previewer = previewerOf(source);
  const open = async () => {
    if (source && (await openSource(source))) setShown(false);
  };
  return (
    <>
      <Popover open={shown} onOpenChange={setShown}>
        <Popover.Trigger>
          <button type="button" className="kw-citation" aria-label={`查看出处 ${index}`}>
            {index}
          </button>
        </Popover.Trigger>
        <Popover.Content width={340} side="top" align="start">
          <SourceContent
            index={index}
            source={source}
            onPreview={previewer ? () => void open() : undefined}
            opening={opening}
          />
        </Popover.Content>
      </Popover>
      {opened ? <FilePreview file={opened.file} locate={opened.locate} onClose={close} /> : null}
    </>
  );
}
