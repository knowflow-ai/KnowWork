/**
 * 执行过程：默认收起成一行摘要，点开后按发生先后列出思考、说明与每一步（照 ChatGPT 的思考面板）。
 * - 单色低调：左侧一条细竖线，正文一律次要色小字，不用彩色状态图标；
 * - 思考照 ChatGPT：边想边在摘要下面滚动显示最新几行（上沿渐隐），正文开始写或这一步结束就收起；
 *   想完的思考收进执行过程，点开按先后看全文（2026-11-10 起；之前只显示「思考中」，不展示思考原文）；
 * - 步骤一行：人话标题（「检索知识库」）+ 目标 + 耗时，进行中的标题带微光；
 *   失败或拒绝只在行尾写一句原因，不变色；参数与结果默认收起，点标题才在代码框里看。
 */
import { useState } from 'react';
import { ChevronDown, ChevronRight } from '@openai/apps-sdk-ui/components/Icon';
import { LoadingIndicator } from '@openai/apps-sdk-ui/components/Indicator';
import { formatDuration, hasDetails, summarizeProcess } from '../events/selectors';
import { prettyText, stepLabel, stepTarget } from '../events/stepLabels';
import type { RunView, StepStatus, StepView, TrailEntry } from '../events/types';

const STATUS_TEXT: Partial<Record<StepStatus, string>> = {
  'awaiting-approval': '等待批准',
  error: '未完成',
  rejected: '已拒绝',
};

function CodeBlock({ label, text }: { readonly label: string; readonly text: string }) {
  return (
    <div className="kw-step-code">
      <div className="kw-step-code-label">{label}</div>
      <pre>{text}</pre>
    </div>
  );
}

function StepRow({ step }: { readonly step: StepView }) {
  const [open, setOpen] = useState(false);
  const args = prettyText(step.arguments);
  const result = prettyText(step.resultPreview);
  const expandable = Boolean(args || result);
  const active = step.status === 'running' || step.status === 'awaiting-approval';
  const elapsed =
    step.startedAt !== undefined && step.endedAt !== undefined
      ? formatDuration(step.endedAt - step.startedAt)
      : '';
  const target = stepTarget(step);
  const notes = [STATUS_TEXT[step.status], step.errorReason].filter(Boolean).join('：');
  return (
    <li className="kw-step">
      <button
        type="button"
        className="kw-step-head"
        aria-expanded={expandable ? open : undefined}
        disabled={!expandable}
        onClick={() => setOpen((v) => !v)}
      >
        <span className={active ? 'kw-step-title kw-shimmer' : 'kw-step-title'}>{stepLabel(step)}</span>
        {target ? <span className="kw-step-target">{target}</span> : null}
        {notes ? <span className="kw-step-note">{notes}</span> : null}
        {elapsed ? <span className="kw-step-extra">{elapsed}</span> : null}
      </button>
      {open && expandable ? (
        <div className="kw-step-detail">
          {args ? <CodeBlock label="参数" text={args} /> : null}
          {result ? <CodeBlock label="结果" text={result} /> : null}
        </div>
      ) : null}
    </li>
  );
}

function TextRow({ text }: { readonly text: string }) {
  return (
    <li className="kw-step">
      <div className="kw-step-text">{text}</div>
    </li>
  );
}

function rowsOf(run: RunView, thinking?: string) {
  const steps = new Map(run.steps.map((step) => [step.callId, step]));
  const fromTrail = run.trail.map((entry: TrailEntry, i) => {
    if (entry.type === 'thinking') return <TextRow key={`thinking-${i}`} text={entry.text} />;
    if (entry.type === 'note') return <TextRow key={`note-${i}`} text={entry.text} />;
    const step = steps.get(entry.callId);
    return step ? <StepRow key={`step-${step.callId}`} step={step} /> : null;
  });
  const problems = run.problems.map((problem, i) => (
    <li key={`problem-${i}`} className="kw-step">
      <div className="kw-step-problem">{problem}</div>
    </li>
  ));
  // 正在想的这一步：思考还没成为正式事件，接在最后
  const current = thinking ? [<TextRow key="thinking-live" text={thinking} />] : [];
  return [...fromTrail, ...current, ...problems];
}

export function ProcessSummary({
  run,
  preparing,
  thinking,
  answering = false,
}: {
  readonly run: RunView;
  /** 模型正在写的工具调用（还没开始跑，events/live.ts） */
  readonly preparing?: readonly string[];
  /** 正在输出的那一步到目前为止的思考（events/live.ts） */
  readonly thinking?: string;
  /** 这一步已经开始写正文：思考预览收起，和 ChatGPT 一样 */
  readonly answering?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const running = run.endedAt === undefined;
  const summary = summarizeProcess(run, preparing);
  const expandable = hasDetails(run) || Boolean(thinking);
  const preview = running && !open && !answering ? thinking : undefined;
  return (
    <div>
      <button
        type="button"
        className="kw-process-toggle"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        disabled={!expandable}
      >
        {running ? <LoadingIndicator size={14} /> : null}
        <span className={running ? 'kw-shimmer' : undefined}>{summary}</span>
        {expandable ? open ? <ChevronDown /> : <ChevronRight /> : null}
      </button>
      {preview ? (
        <div className="kw-thinking-preview">
          <div className="kw-step-text">{preview}</div>
        </div>
      ) : null}
      {open && expandable ? (
        <ol className="kw-process-body">{rowsOf(run, running ? thinking : undefined)}</ol>
      ) : null}
    </div>
  );
}
