/**
 * 执行过程：默认收起成一行摘要，点开后按发生先后列出说明与每一步（照 ChatGPT 的思考面板）。
 * - 单色低调：左侧一条细竖线，正文一律次要色小字，不用彩色状态图标；
 * - 模型思考原文不展示：进行中只显示带微光的「思考中」；给用户看的是模型调工具前写的说明；
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

function rowsOf(run: RunView) {
  const steps = new Map(run.steps.map((step) => [step.callId, step]));
  const fromTrail = run.trail.map((entry: TrailEntry, i) => {
    if (entry.type === 'thinking') return null;
    if (entry.type === 'note') return <TextRow key={`note-${i}`} text={entry.text} />;
    const step = steps.get(entry.callId);
    return step ? <StepRow key={`step-${step.callId}`} step={step} /> : null;
  });
  const problems = run.problems.map((problem, i) => (
    <li key={`problem-${i}`} className="kw-step">
      <div className="kw-step-problem">{problem}</div>
    </li>
  ));
  return [...fromTrail, ...problems];
}

export function ProcessSummary({
  run,
  preparing,
}: {
  readonly run: RunView;
  /** 模型正在写的工具调用（还没开始跑，events/live.ts） */
  readonly preparing?: readonly string[];
}) {
  const [open, setOpen] = useState(false);
  const running = run.endedAt === undefined;
  const summary = summarizeProcess(run, preparing);
  const expandable = hasDetails(run);
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
      {open && expandable ? <ol className="kw-process-body">{rowsOf(run)}</ol> : null}
    </div>
  );
}
