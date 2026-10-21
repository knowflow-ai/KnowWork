/**
 * 一轮的运行详情（运行调试视图）。布局照 OpenAI 平台的 Traces：顶部是这一轮的提问与汇总，
 * 左栏每一步一行带用时条，右栏是选中那一步的输出与工具调用，每一步可以看按日志还原的输入；原始日志可下载。
 * 会话本人从对话里打开；管理员从「运行记录」打开（会记审计）。
 */
import { useState, type ReactNode } from 'react';
import { Button, ButtonLink } from '@openai/apps-sdk-ui/components/Button';
import { ArrowLeftSm, ArrowRightSm, Download, PauseSm, PlaySm } from '@openai/apps-sdk-ui/components/Icon';
import { Tooltip } from '@openai/apps-sdk-ui/components/Tooltip';
import { useQuery } from '@tanstack/react-query';
import { readableError } from '../../api/http';
import { exportUrl, getTurn, type DebugStep, type RunSource, type TurnDetail } from '../../api/debug';
import { Sheet } from '../../ui/Sheet';
import { Skeleton } from '../../ui/Skeleton';
import { duration, statusLabel } from './format';
import { StepDetail } from './StepDetail';
import { StepInputDialog } from './StepInputDialog';
import { StepList, type StepGroup, type StepRef } from './StepList';
import { useReplay } from './useReplay';

/** 汇总：这一轮的提问，下面一行说明色小字；状态只写文字不上色（同对话里的执行过程摘要）。 */
function Summary({ detail }: { readonly detail: TurnDetail }) {
  const s = detail.summary;
  const meta = [
    statusLabel(s.status),
    `${s.steps} 步`,
    `调用工具 ${s.toolCalls} 次`,
    `用时 ${duration(s.durationMs)}`,
    `输入 ${s.inputTokens} · 输出 ${s.outputTokens} token`,
    s.cost ? `费用 ${s.cost}` : '',
    s.errors > 0 ? `失败 ${s.errors} 处` : '',
  ].filter(Boolean);
  return (
    <header className="kw-trace-summary">
      <p className="kw-trace-request">{s.request || '（没有找到这一轮的提问）'}</p>
      <div className="kw-trace-meta">{meta.join(' · ')}</div>
      {detail.events.length > 0 ? <div className="kw-trace-meta">{detail.events.join(' · ')}</div> : null}
    </header>
  );
}

function ReplayBar({
  replay,
  total,
}: {
  readonly replay: ReturnType<typeof useReplay>;
  readonly total: number;
}) {
  const icon = (label: string, child: ReactNode, onClick: () => void, disabled: boolean) => (
    <Tooltip content={label} compact>
      <Button
        color="secondary"
        variant="ghost"
        size="sm"
        uniform
        aria-label={label}
        disabled={disabled}
        onClick={onClick}
      >
        {child}
      </Button>
    </Tooltip>
  );
  return (
    <div className="kw-trace-replay" role="toolbar" aria-label="逐步回放">
      <span className="kw-trace-meta">
        第 {Math.min(replay.shown, total)} / {total} 步
      </span>
      {icon('上一步', <ArrowLeftSm />, replay.back, replay.shown <= 1)}
      {icon(
        replay.playing ? '暂停回放' : '从头回放',
        replay.playing ? <PauseSm /> : <PlaySm />,
        replay.toggle,
        total === 0,
      )}
      {icon('下一步', <ArrowRightSm />, replay.forward, replay.shown >= total)}
    </div>
  );
}

function find(
  groups: readonly StepGroup[],
  ref: StepRef | undefined,
): { step: DebugStep; group: StepGroup } | undefined {
  if (!ref) return undefined;
  const group = groups.find((g) => g.agent === ref.agent);
  const step = group?.steps.find((s) => s.step === ref.step);
  return group && step ? { step, group } : undefined;
}

function Trace({
  detail,
  onInput,
}: {
  readonly detail: TurnDetail;
  readonly onInput: (step: number) => void;
}) {
  const replay = useReplay(detail.steps.length);
  const [picked, setPicked] = useState<StepRef>();
  const main = detail.steps.slice(0, replay.replaying ? replay.shown : detail.steps.length);
  const groups: StepGroup[] = [
    { agent: null, steps: main },
    ...detail.subagents.map((a) => ({ agent: a.sessionId, label: `子 Agent：${a.label}`, steps: a.steps })),
  ];
  // 回放时跟着最新的一步走；平时停在用户点的那一步，没点过就是第一步
  const latest = main[main.length - 1];
  const fallback = replay.replaying && latest ? { agent: null, step: latest.step } : picked;
  const selected = find(groups, fallback) ?? find(groups, main[0] && { agent: null, step: main[0].step });
  return (
    <div className="kw-trace">
      <Summary detail={detail} />
      <div className="kw-trace-split">
        <nav className="kw-trace-list" aria-label="运行步骤">
          <ReplayBar replay={replay} total={detail.steps.length} />
          {detail.steps.length === 0 ? <p className="kw-trace-meta">这一轮还没有步骤</p> : null}
          <StepList
            groups={groups}
            selected={selected && { agent: selected.group.agent, step: selected.step.step }}
            onSelect={setPicked}
          />
        </nav>
        <div className="kw-trace-detail">
          {selected ? (
            <StepDetail
              key={`${selected.group.agent ?? 'main'}-${selected.step.step}`}
              step={selected.step}
              label={selected.group.label}
              onInput={selected.group.agent === null ? () => onInput(selected.step.step) : undefined}
            />
          ) : null}
        </div>
      </div>
    </div>
  );
}

export default function RunInspector({
  source,
  turn,
  title,
  onClose,
}: {
  readonly source: RunSource;
  readonly turn: number;
  /** 不给就是「第 N 轮运行详情」。 */
  readonly title?: string;
  readonly onClose: () => void;
}) {
  const detail = useQuery({
    queryKey: [...source.key, 'turn', turn],
    queryFn: () => getTurn(source, turn),
  });
  const [inputStep, setInputStep] = useState<number>();
  return (
    <Sheet
      title={title ?? `第 ${turn} 轮运行详情`}
      width={1100}
      flush
      onClose={onClose}
      extra={
        <ButtonLink
          as="a"
          href={exportUrl(source, turn)}
          download
          color="secondary"
          variant="outline"
          size="sm"
        >
          <Download />
          导出日志
        </ButtonLink>
      }
    >
      {detail.isPending ? <Skeleton rows={10} className="kw-trace-state" /> : null}
      {detail.isError ? (
        <p className="kw-trace-problem kw-trace-state">
          {readableError(detail.error, '运行详情没有加载出来，请稍后重试')}
        </p>
      ) : null}
      {detail.data ? <Trace detail={detail.data} onInput={setInputStep} /> : null}
      {inputStep !== undefined ? (
        <StepInputDialog
          source={source}
          turn={turn}
          step={inputStep}
          onClose={() => setInputStep(undefined)}
        />
      ) : null}
    </Sheet>
  );
}
