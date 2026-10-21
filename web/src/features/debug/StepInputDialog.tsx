/**
 * 某一步发给模型的输入：系统提示、工具清单、这一步之前的全部消息。按会话日志还原，顶部一句小字说明和实际请求的差别。
 * 样式同运行详情：单色小字、灰底框，不用彩色提示框与标签。
 */
import { useState, type ReactNode } from 'react';
import { ChevronDown, ChevronRight } from '@openai/apps-sdk-ui/components/Icon';
import { useQuery } from '@tanstack/react-query';
import { readableError } from '../../api/http';
import { getStepInput, type DebugMessage, type RunSource } from '../../api/debug';
import { messageLabel, prettyJson } from './format';
import { Dialog } from '../../ui/Dialog';
import { Skeleton } from '../../ui/Skeleton';
import { Block } from './Block';

function Section({ title, children }: { readonly title: string; readonly children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button
        type="button"
        className="kw-process-toggle"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        {open ? <ChevronDown /> : <ChevronRight />}
        {title}
      </button>
      {open ? <div className="kw-trace-section">{children}</div> : null}
    </div>
  );
}

function Message({ message }: { readonly message: DebugMessage }) {
  const who = messageLabel(message.role, message.source) + (message.isError ? ' · 出错' : '');
  return (
    <div className="kw-trace-message">
      {message.blocks.map((block, index) => {
        const kind =
          block.type === 'reasoning' ? ' · 思考' : block.type === 'tool-call' ? ` · 调用 ${block.name}` : '';
        const label = (index === 0 ? who : '') + kind + (block.truncated ? ' · 已截断' : '');
        return (
          <Block
            key={index}
            label={label || who}
            text={block.type === 'tool-call' ? prettyJson(block.text) : block.text}
            code={block.type === 'tool-call'}
          />
        );
      })}
    </div>
  );
}

export function StepInputDialog({
  source,
  turn,
  step,
  onClose,
}: {
  readonly source: RunSource;
  readonly turn: number;
  readonly step: number;
  readonly onClose: () => void;
}) {
  const input = useQuery({
    queryKey: [...source.key, 'input', turn, step],
    queryFn: () => getStepInput(source, turn, step),
  });
  return (
    <Dialog title={`第 ${step} 步的输入`} size="lg" onClose={onClose}>
      {input.isPending ? <Skeleton rows={8} /> : null}
      {input.isError ? (
        <p className="kw-trace-problem">{readableError(input.error, '输入没有加载出来，请稍后重试')}</p>
      ) : null}
      {input.data ? (
        <div className="kw-trace-input">
          {input.data.notes.map((note) => (
            <div key={note} className="kw-trace-meta">
              {note}
            </div>
          ))}
          <Section title={`系统提示（${input.data.system.length} 段）`}>
            {input.data.system.map((text, i) => (
              <Block key={i} label={`第 ${i + 1} 段`} text={text} />
            ))}
          </Section>
          <Section title={`可用工具（${input.data.tools.length} 个）`}>
            {input.data.tools.map((tool) => (
              <div key={tool.name} className="kw-trace-meta">
                <span className="kw-trace-strong">{tool.name}</span> {tool.description}
              </div>
            ))}
          </Section>
          <div className="kw-trace-meta">消息 {input.data.messages.length} 条，按时间顺序</div>
          {input.data.messages.map((message, index) => (
            <Message key={index} message={message} />
          ))}
        </div>
      ) : null}
    </Dialog>
  );
}
