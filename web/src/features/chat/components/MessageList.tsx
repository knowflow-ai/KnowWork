/** 消息列表：按时间线渲染用户气泡与助手轮次，新内容出现时滚到底部。 */
import { AttachmentChips } from './AttachmentChips';
import { MentionChips } from '../mentions/MentionChips';
import { useEffect, useRef } from 'react';
import type { QuestionAnswer } from '../../../api/conversations';
import { selectRunApprovals, selectRunQuestions, selectRunState, selectTimeline } from '../events/selectors';
import type { ChatState } from '../events/types';
import { ApprovalCard } from './ApprovalCard';
import { AssistantTurn } from './AssistantTurn';
import { QuestionCard } from './QuestionCard';
import type { FeedbackControls } from '../hooks/useFeedback';

interface Props {
  readonly state: ChatState;
  readonly onDecide: (approvalId: string, allow: boolean, remember?: boolean) => Promise<void>;
  /** 返回是否提交成功（失败已由页面提示）。 */
  readonly onAnswer: (questionId: string, answers: readonly QuestionAnswer[]) => Promise<boolean>;
  readonly onRegenerate: () => void;
  /** 撤掉最近一轮回答上方的检索条件、重新回答（参数是这一轮要去掉的全部条件）。 */
  readonly onDropConditions?: (dropped: readonly string[]) => void;
  /** 打开某一轮的运行详情（参数是 events.v1 的 run_id）。 */
  readonly onInspect?: (runId: string) => void;
  /** 每轮回答的赞、踩；没有会话时不给，回答下面就不显示赞、踩。 */
  readonly feedback?: FeedbackControls;
}

export function MessageList({
  state,
  onDecide,
  onAnswer,
  onRegenerate,
  onDropConditions,
  onInspect,
  feedback,
}: Props) {
  const endRef = useRef<HTMLDivElement>(null);
  const busy = selectRunState(state) !== 'idle';
  const timeline = selectTimeline(state);
  const lastRunId = state.runOrder[state.runOrder.length - 1];

  const liveLength = state.live?.text.length ?? 0;
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: 'end' });
  }, [state.lastId, state.sent.length, liveLength]);

  return (
    <div className="kw-chat-column">
      {timeline.map((entry) => {
        if (entry.type === 'user') {
          const sending = entry.message.status === 'sending';
          return (
            <div key={`u-${entry.message.localId}`} className="kw-user-row">
              <AttachmentChips items={entry.message.attachments ?? []} label="这条消息带的附件" />
              <MentionChips items={entry.message.mentions ?? []} label="这条消息 @ 点名的范围与技能" />
              {entry.message.text ? (
                <div
                  className={`kw-user-bubble${sending ? ' is-pending' : ''}`}
                  aria-label={sending ? '正在发送' : undefined}
                >
                  {entry.message.text}
                </div>
              ) : null}
            </div>
          );
        }
        const run = state.runs[entry.runId];
        if (!run) return null;
        return (
          <AssistantTurn
            key={`r-${entry.runId}`}
            run={run}
            approvals={selectRunApprovals(state, entry.runId)}
            questions={selectRunQuestions(state, entry.runId)}
            artifacts={state.artifacts.filter(
              // 没记下轮次的产物（旧数据）摆在最后一轮下面，免得看不到
              (a) => a.runId === entry.runId || (a.runId === undefined && entry.runId === lastRunId),
            )}
            sources={state.citations}
            onDecide={onDecide}
            onAnswer={onAnswer}
            onRegenerate={entry.runId === lastRunId ? onRegenerate : undefined}
            onDropConditions={entry.runId === lastRunId && !busy ? onDropConditions : undefined}
            onInspect={onInspect ? () => onInspect(entry.runId) : undefined}
            busy={busy}
            liveText={state.live?.runId === entry.runId ? state.live.text : undefined}
            feedback={feedback?.byRun[entry.runId]?.rating ?? null}
            onFeedback={feedback ? (input) => feedback.submit(entry.runId, input) : undefined}
            onRetract={feedback ? () => feedback.retract(entry.runId) : undefined}
          />
        );
      })}
      {state.approvals
        .filter((a) => a.runId === undefined)
        .map((approval) => (
          <ApprovalCard
            key={approval.id}
            approval={approval}
            onDecide={(allow, remember) => onDecide(approval.id, allow, remember)}
          />
        ))}
      {state.questions
        .filter((q) => q.runId === undefined)
        .map((question) => (
          <QuestionCard
            key={question.id}
            question={question}
            onAnswer={(answers) => onAnswer(question.id, answers)}
          />
        ))}
      <div ref={endRef} />
    </div>
  );
}
