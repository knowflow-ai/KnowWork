/**
 * 运行详情左栏：每一步一行，写明做了什么、用时，下面一道灰色用时条（按本轮最长的一步折算），
 * 一眼看出时间花在哪。子 Agent 的步骤分组列在后面。单色，失败只在行尾写「有失败」。
 */
import type { DebugStep } from '../../api/debug';
import { cx } from '../../ui/cx';
import { duration, stepFailed, stepTarget } from './format';

export interface StepRef {
  /** 子 Agent 的会话 id；主流程是 null。 */
  readonly agent: string | null;
  readonly step: number;
}

export interface StepGroup {
  readonly agent: string | null;
  readonly label?: string;
  readonly steps: readonly DebugStep[];
}

const same = (a: StepRef | undefined, b: StepRef) => a?.agent === b.agent && a.step === b.step;

export function StepList({
  groups,
  selected,
  onSelect,
}: {
  readonly groups: readonly StepGroup[];
  readonly selected?: StepRef;
  readonly onSelect: (ref: StepRef) => void;
}) {
  const longest = Math.max(1, ...groups.flatMap((g) => g.steps.map((s) => s.durationMs ?? 0)));
  return (
    <>
      {groups.map((group) => (
        <section key={group.agent ?? 'main'} className="kw-trace-group">
          {group.label ? <div className="kw-trace-group-label">{group.label}</div> : null}
          <ol className="kw-trace-steps" aria-label={group.label ?? '步骤'}>
            {group.steps.map((step) => {
              const ref = { agent: group.agent, step: step.step };
              const share = Math.max(2, ((step.durationMs ?? 0) / longest) * 100);
              return (
                <li key={step.step}>
                  <button
                    type="button"
                    className={cx('kw-trace-row', same(selected, ref) && 'is-selected')}
                    aria-current={same(selected, ref) ? 'step' : undefined}
                    onClick={() => onSelect(ref)}
                  >
                    <span className="kw-trace-row-line">
                      <span className="kw-trace-row-title">第 {step.step} 步</span>
                      <span className="kw-trace-row-target">{stepTarget(step)}</span>
                      <span className="kw-trace-row-time">{duration(step.durationMs)}</span>
                    </span>
                    <span className="kw-trace-row-line">
                      <span className="kw-trace-bar" aria-hidden="true">
                        <span style={{ width: `${share}%` }} />
                      </span>
                      {stepFailed(step) ? <span className="kw-trace-row-problem">有失败</span> : null}
                    </span>
                  </button>
                </li>
              );
            })}
          </ol>
        </section>
      ))}
    </>
  );
}
