/**
 * 长任务目标的进度（照 DSH 官方网页界面的目标面板）：常驻在输入框上方，写目标、现在怎么样、自动接着做到第几轮，
 * 带暂停、恢复、清除。按钮执行的就是官方 /goal 命令（和在输入框里敲一样），结果随事件回来更新这里。
 * 已完成的也留着，直到清除或设新目标。
 */
import { useState } from 'react';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { Flag } from '@openai/apps-sdk-ui/components/Icon';
import { goalReasonText } from '../events/goalText';
import type { GoalView } from '../events/types';

// 用词照官方目标栏的中文词条（dsh-client-ui-goal）：进行中、待继续、已暂停、受阻
const STATUS: Readonly<Record<GoalView['phase'], string>> = {
  active: '进行中',
  paused: '已暂停',
  blocked: '受阻',
  complete: '已完成',
};

export function GoalPanel({
  goal,
  run,
}: {
  readonly goal: GoalView;
  /** 执行一行 /goal 命令；不给就只显示、没有按钮（例如这段对话在别的电脑上执行） */
  readonly run?: (line: string) => Promise<void>;
}) {
  const [pending, setPending] = useState(false);
  const act = async (line: string) => {
    if (!run) return;
    setPending(true);
    try {
      await run(line);
    } finally {
      setPending(false);
    }
  };
  const button = (label: string, line: string) => (
    <Button color="secondary" variant="ghost" size="xs" disabled={pending} onClick={() => void act(line)}>
      {label}
    </Button>
  );
  const progress = goal.maxRounds > 0 ? `自动接着做了 ${goal.rounds} / ${goal.maxRounds} 轮` : null;
  // 进行中但自动续跑关着（宿主重启、对话重新打开过，官方先停下，要人确认才接着做）
  const stalled = goal.phase === 'active' && goal.armed === false;
  return (
    <section className="kw-goal" aria-label="长任务目标">
      <div className="kw-goal-head">
        <Flag aria-hidden />
        <span className="kw-goal-title">长任务目标</span>
        <span className={`kw-goal-status is-${goal.phase}`}>{stalled ? '待继续' : STATUS[goal.phase]}</span>
        {progress ? <span className="kw-goal-progress">{progress}</span> : null}
        <span className="kw-goal-actions">
          {run && goal.phase === 'active' && !stalled ? button('暂停', '/goal pause') : null}
          {run && (stalled || goal.phase === 'paused' || goal.phase === 'blocked')
            ? button('恢复', '/goal resume')
            : null}
          {run ? button('清除', '/goal clear') : null}
        </span>
      </div>
      <p className="kw-goal-objective">{goal.objective}</p>
      {goal.phase === 'blocked' && goal.blockedReason ? (
        <p className="kw-goal-reason">原因：{goalReasonText(goal.blockedReason)}</p>
      ) : null}
      {stalled ? (
        <p className="kw-goal-reason">对话重新打开过，自动接着做先停下了；点「恢复」接着做</p>
      ) : null}
    </section>
  );
}
