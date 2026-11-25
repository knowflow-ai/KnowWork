/**
 * 对话里的一次上下文压缩（照 DSH 官方对话界面：一次压缩一行）：
 * - 进行中：「正在压缩上下文…」；
 * - 做完：一条分隔线，中间写「上面的对话已压缩成摘要（N 条记录，约 T tokens），模型从这里往后只看摘要」，点「查看摘要」
 *   在右侧抽屉里看摘要正文（官方在侧栏标签里打开）；
 * - 出错：写原因；结束了却没写出摘要（没东西可压）的不显示。
 */
import { useState } from 'react';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { ChevronRight } from '@openai/apps-sdk-ui/components/Icon';
import { LoadingIndicator } from '@openai/apps-sdk-ui/components/Indicator';
import { Sheet } from '../../../ui/Sheet';
import { compactedAmount } from '../events/compactions';
import type { CompactionView } from '../events/types';
import { Markdown } from './Markdown';

export function CompactionNotice({ compaction }: { readonly compaction: CompactionView }) {
  const [open, setOpen] = useState(false);
  if (compaction.status === 'ended') return null;
  if (compaction.status === 'running') {
    return (
      <div className="kw-compaction" role="status">
        <span className="kw-compaction-body">
          <LoadingIndicator size={14} />
          正在压缩上下文…
        </span>
      </div>
    );
  }
  if (compaction.status === 'error') {
    return (
      <div className="kw-compaction" role="status">
        <span className="kw-compaction-body">上下文压缩没有完成：{compaction.error ?? '原因不明'}</span>
      </div>
    );
  }
  return (
    <div className="kw-compaction" role="note">
      <span className="kw-compaction-body">
        上面的对话已压缩成摘要{compactedAmount(compaction)}，模型从这里往后只看摘要
        {compaction.summary ? (
          <Button color="primary" variant="ghost" size="sm" onClick={() => setOpen(true)}>
            查看摘要
            <ChevronRight aria-hidden />
          </Button>
        ) : null}
      </span>
      {open && compaction.summary ? (
        <Sheet title="压缩摘要" width={560} onClose={() => setOpen(false)}>
          <Markdown text={compaction.summary} />
        </Sheet>
      ) : null}
    </div>
  );
}
