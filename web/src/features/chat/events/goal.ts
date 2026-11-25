/**
 * 长任务目标（官方 dsh-goal）：服务端把会话日志里的 goal/change（变化后的整份目标，或清除）和自动续跑每一轮的轮数，
 * 投影成同一个 goal 条目（events_v1.py）。整份的直接换上，只带轮数的合进已有的那份；清除了就没有目标。
 */
import type { ChatState, GoalView, V1Event } from './types';

const PHASES: readonly GoalView['phase'][] = ['active', 'paused', 'blocked', 'complete'];

export function onGoal(state: ChatState, event: V1Event): ChatState {
  const data = event.data;
  if (data.cleared === true) return { ...state, goal: undefined };
  const rounds = typeof data.rounds === 'number' ? data.rounds : undefined;
  const phase = PHASES.find((p) => p === data.phase);
  if (phase && typeof data.objective === 'string') {
    const reason = typeof data.blocked_reason === 'string' ? data.blocked_reason : undefined;
    const armed = state.goal?.armed;
    return {
      ...state,
      goal: {
        objective: data.objective,
        phase,
        ...(phase === 'blocked' && reason ? { blockedReason: reason } : {}),
        rounds: Math.max(rounds ?? 0, 0),
        maxRounds: typeof data.max_rounds === 'number' ? data.max_rounds : 0,
        // 开没开自动续跑跟着随后的那条走（目标变化后宿主紧接着报）；先沿用上一次的
        ...(armed === undefined ? {} : { armed }),
      },
    };
  }
  if (typeof data.armed === 'boolean') {
    return state.goal ? { ...state, goal: { ...state.goal, armed: data.armed } } : state;
  }
  // 自动续跑又发起了一轮：只更新轮数
  if (rounds !== undefined && state.goal) return { ...state, goal: { ...state.goal, rounds } };
  return state;
}
