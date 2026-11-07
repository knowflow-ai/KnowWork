/** 消息列表：按时间线渲染用户气泡、定时任务这类自动发起的通知与助手轮次，新内容出现时滚到底部。 */
import { AttachmentChips } from './AttachmentChips';
import { MentionChips } from '../mentions/MentionChips';
import { useEffect, useRef } from 'react';
import { LoadingIndicator } from '@openai/apps-sdk-ui/components/Indicator';
import type { QuestionAnswer } from '../../../api/conversations';
import {
  selectAwaitingReply,
  selectRunApprovals,
  selectRunQuestions,
  selectRunState,
  selectTimeline,
} from '../events/selectors';
import type { ChatState } from '../events/types';
import { ApprovalCard } from './ApprovalCard';
import { AssistantTurn } from './AssistantTurn';
import { QuestionCard } from './QuestionCard';
import { TriggerNotice } from './TriggerNotice';
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
  /** 在这个对话里问一句（结果卡片上接着问的建议点了用） */
  readonly onAsk?: (text: string) => void;
}

export function MessageList({
  state,
  onDecide,
  onAnswer,
  onRegenerate,
  onDropConditions,
  onInspect,
  feedback,
  onAsk,
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
              <MentionChips items={entry.message.mentions ?? []} label="这条消息 @ 点名的内容" />
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
        if (entry.type === 'trigger') {
          return <TriggerNotice key={`t-${entry.trigger.id}`} trigger={entry.trigger} />;
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
            onAsk={onAsk}
            liveText={state.live?.runId === entry.runId ? state.live.text : undefined}
            liveTools={state.live?.runId === entry.runId ? state.live.tools : undefined}
            feedback={feedback?.byRun[entry.runId]?.rating ?? null}
            onFeedback={feedback ? (input) => feedback.submit(entry.runId, input) : undefined}
            onRetract={feedback ? () => feedback.retract(entry.runId) : undefined}
          />
        );
      })}
      {selectAwaitingReply(state) ? (
        // 和一轮回答开头的进度一样：转圈加带微光的「思考中」
        <div className="kw-assistant" aria-label="助手回答">
          <div className="kw-process-pending" role="status">
            <LoadingIndicator size={14} />
            <span className="kw-shimmer">思考中</span>
          </div>
        </div>
      ) : null}
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
