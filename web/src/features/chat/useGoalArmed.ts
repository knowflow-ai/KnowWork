/**
 * 长任务目标的自动续跑此刻开着没有（官方目标栏的「待继续」靠它）。官方只记在执行的一方的内存里、不进会话日志：
 * 会话重开、宿主重启后先关上，要用户「恢复」才再开。所以不从事件里推，由对话来源现取（云端问业务服务）。
 * 目标进行中时才问；目标变了（阶段、轮数）、开始或停下跑时重新问。来源不给、还没问到时是 undefined，面板照事件里的显示
 */
import { useQuery } from '@tanstack/react-query';
import type { GoalView } from './events/types';
import type { ConversationActions } from './sources';

export function useGoalArmed(
  sourceId: string,
  conversationId: string,
  goal: GoalView | undefined,
  fetchArmed: ConversationActions['goalArmed'],
  running: boolean,
): boolean | undefined {
  const active = goal?.phase === 'active' && fetchArmed !== undefined;
  const query = useQuery({
    queryKey: ['goal-armed', sourceId, conversationId, goal?.objective, goal?.phase, goal?.rounds, running],
    queryFn: () => (fetchArmed ? fetchArmed(conversationId) : Promise.resolve(false)),
    enabled: active,
    retry: false,
  });
  return active ? query.data : undefined;
}
