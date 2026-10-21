/**
 * 运行详情右栏：选中那一步的全部内容。顶部一行写模型、用量、用时，右侧「查看这一步的输入」；
 * 下面依次是失败原因、思考过程（默认收起）、模型输出、每次工具调用的参数与结果。单色，不用彩色状态与提示框。
 */
import { useState } from 'react';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { ChevronDown, ChevronRight } from '@openai/apps-sdk-ui/components/Icon';
import type { DebugStep, DebugToolCall } from '../../api/debug';
import { Block } from './Block';
import { duration, prettyJson, stepTarget } from './format';

function ToolCall({ call }: { readonly call: DebugToolCall }) {
  const notes = [
    duration(call.durationMs),
    call.isError ? '出错' : '',
    call.truncated ? '内容太长，已截断' : '',
  ]
    .filter(Boolean)
    .join(' · ');
  return (
    <section className="kw-trace-call">
      <div className="kw-trace-call-head">
        <span className="kw-trace-strong">调用 {call.name}</span>
        <span className="kw-trace-meta">{notes}</span>
      </div>
      <Block label="参数" text={prettyJson(call.arguments) || '（无）'} code />
      <Block label="结果" text={call.result ?? '（没有收到结果）'} code />
    </section>
  );
}

function usageLine(step: DebugStep): string {
  const usage = step.usage;
  if (!usage) return `用时 ${duration(step.durationMs)}`;
  return [
    usage.model,
    `输入 ${usage.inputTokens} · 输出 ${usage.outputTokens} token`,
    `用时 ${duration(usage.durationMs ?? step.durationMs)}`,
    usage.cost ? `费用 ${usage.cost}` : '',
  ]
    .filter(Boolean)
    .join(' · ');
}

export function StepDetail({
  step,
  label,
  onInput,
}: {
  readonly step: DebugStep;
  /** 子 Agent 的步骤带上它的名字。 */
  readonly label?: string;
  readonly onInput?: () => void;
}) {
  const [thinking, setThinking] = useState(false);
  return (
    <article className="kw-trace-step" aria-label={`第 ${step.step} 步详情`}>
      <header className="kw-trace-step-head">
        <div className="kw-trace-step-title">
          <h3>
            {label ? `${label} · ` : ''}第 {step.step} 步 · {stepTarget(step)}
          </h3>
          <div className="kw-trace-meta">{usageLine(step)}</div>
        </div>
        {onInput ? (
          <Button color="secondary" variant="outline" size="sm" onClick={onInput}>
            查看这一步的输入
          </Button>
        ) : null}
      </header>
      {step.failures.map((failure) => (
        <p key={failure} className="kw-trace-problem">
          {failure}
        </p>
      ))}
      {step.reasoning.trim() ? (
        <div>
          <button
            type="button"
            className="kw-process-toggle"
            aria-expanded={thinking}
            onClick={() => setThinking((v) => !v)}
          >
            {thinking ? <ChevronDown /> : <ChevronRight />}
            思考过程
          </button>
          {thinking ? <pre className="kw-trace-thinking">{step.reasoning.trim()}</pre> : null}
        </div>
      ) : null}
      {step.text.trim() ? <Block label="模型输出" text={step.text} /> : null}
      {step.toolCalls.map((call) => (
        <ToolCall key={call.callId} call={call} />
      ))}
    </article>
  );
}
