/** 消息列表：按时间线渲染用户气泡、定时任务这类自动发起的通知与助手轮次，新内容出现时滚到底部。 */
import { AttachmentChips } from './AttachmentChips';
import type { AttachmentChip } from '../../../api/attachments';
import { previewKindOf } from '../../preview/previewKind';
import { useFileOpener } from './fileLinkContext';
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
import { CommandNotice } from './CommandNotice';
import { CompactionNotice } from './CompactionNotice';
import type { FeedbackControls } from '../hooks/useFeedback';

interface Props {
  readonly state: ChatState;
  readonly onDecide: (approvalId: string, allow: boolean, remember?: boolean) => Promise<void>;
  /** 审批卡片上给不给「本会话内同类操作不再询问」（见 sources.ts 的 remembersApprovals），默认给 */
  readonly rememberable?: boolean;
  /** 返回是否提交成功（失败已由页面提示）。 */
  readonly onAnswer: (questionId: string, answers: readonly QuestionAnswer[]) => Promise<boolean>;
  /** 重新生成最后一轮；不给就不显示（例如这段对话在别的电脑上执行，这里只能看） */
  readonly onRegenerate?: () => void;
  /** 撤掉最近一轮回答上方的检索条件、重新回答（参数是这一轮要去掉的全部条件）。 */
  readonly onDropConditions?: (dropped: readonly string[]) => void;
  /** 打开某一轮的运行详情（参数是 events.v1 的 run_id）。 */
  readonly onInspect?: (runId: string) => void;
  /** 每轮回答的赞、踩；没有会话时不给，回答下面就不显示赞、踩。 */
  readonly feedback?: FeedbackControls;
  /** 在这个对话里问一句（结果卡片上接着问的建议点了用） */
  readonly onAsk?: (text: string) => void;
  /** 跟着最新的（停在底部附近）：新内容来了滚到底；往上翻了是 false，不拽走（自己发消息时照样回到底部） */
  readonly follow?: boolean;
}

/** 消息上的附件：带路径、能预览的点得开 */
function sentAttachmentOpener(
  open: ((source: { readonly path: string }) => void) | undefined,
): ((item: AttachmentChip) => (() => void) | undefined) | undefined {
  if (!open) return undefined;
  return ({ name, path }) =>
    path !== undefined && previewKindOf(name) !== 'none' ? () => open({ path }) : undefined;
}

export function MessageList({
  state,
  onDecide,
  rememberable = true,
  onAnswer,
  onRegenerate,
  onDropConditions,
  onInspect,
  feedback,
  onAsk,
  follow = true,
}: Props) {
  const endRef = useRef<HTMLDivElement>(null);
  // 消息上的附件同回答里的文件链接、交付卡片：这里打得开（能继续这段对话）时才点得开
  const openAttachment = sentAttachmentOpener(useFileOpener()?.openAttachment);
  const busy = selectRunState(state) !== 'idle';
  const timeline = selectTimeline(state);
  const lastRunId = state.runOrder[state.runOrder.length - 1];

  const liveLength = (state.live?.text.length ?? 0) + (state.live?.reasoning.length ?? 0);
  // 自己刚发了消息：不管翻到哪都回到底部；别的新内容只在跟着最新的时候滚
  const sentCount = state.sent.length;
  const seenSent = useRef(sentCount);
  useEffect(() => {
    const justSent = sentCount !== seenSent.current;
    seenSent.current = sentCount;
    if (follow || justSent) endRef.current?.scrollIntoView({ block: 'end' });
  }, [state.lastId, sentCount, liveLength, follow]);

  return (
    <div className="kw-chat-column">
      {timeline.map((entry) => {
        if (entry.type === 'user') {
          const sending = entry.message.status === 'sending';
          return (
            <div key={`u-${entry.message.localId}`} className="kw-user-row">
              <AttachmentChips
                items={entry.message.attachments ?? []}
                openerOf={openAttachment}
                label="这条消息带的附件"
              />
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
        if (entry.type === 'command') {
          return <CommandNotice key={`c-${entry.command.id}`} command={entry.command} />;
        }
        if (entry.type === 'compaction') {
          return <CompactionNotice key={`k-${entry.compaction.id}`} compaction={entry.compaction} />;
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
            sources={state.citations}
            onDecide={onDecide}
            rememberable={rememberable}
            onAnswer={onAnswer}
            onRegenerate={entry.runId === lastRunId ? onRegenerate : undefined}
            onDropConditions={entry.runId === lastRunId && !busy ? onDropConditions : undefined}
            onInspect={onInspect ? () => onInspect(entry.runId) : undefined}
            busy={busy}
            onAsk={onAsk}
            liveText={state.live?.runId === entry.runId ? state.live.text : undefined}
            liveTools={state.live?.runId === entry.runId ? state.live.tools : undefined}
            liveReasoning={state.live?.runId === entry.runId ? state.live.reasoning : undefined}
            feedback={feedback?.byRun[entry.runId]?.rating ?? null}
            onFeedback={feedback ? (input) => feedback.submit(entry.runId, input) : undefined}
            onRetract={feedback ? () => feedback.retract(entry.runId) : undefined}
          />
        );
      })}
      {selectAwaitingReply(state) ? (
        // 和一轮回答开头的进度一样：转圈加带微光的「思考中」
        <div className="kw-assistant" aria-label="智能体回答">
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
            rememberable={rememberable}
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
