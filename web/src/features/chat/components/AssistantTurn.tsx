/**
 * 一轮助手回答：执行过程一行、待办、审批、正文（不套气泡）、文件、工具的说明（如已记住）、结束说明、操作图标。
 * 中途向用户提问时照 ChatGPT「问一句 → 你答 → 接着做」的节奏，在提问处切成两段（events/segments.ts）：提问之前的工作
 * 一行过程、下面是提问卡片；答完后你的回答画成一条用户气泡，后面的工作另起一行过程，紧跟着最终回答。
 * 还在干活时，调工具之前写的进度说明按先后显示在过程那一行下面，一轮结束后收进过程，只留最终回答。
 */
import { Fragment, useMemo } from 'react';
import type { QuestionAnswer } from '../../../api/conversations';
import type { ApprovalView, CitationSource, QuestionView, RunView } from '../events/types';
import { AnswerActions } from './AnswerActions';
import { AnswerNotices } from './AnswerNotices';
import { ApprovalCard } from './ApprovalCard';
import { AnswerSources } from './AnswerSources';
import { citedFiles, citedNumbers } from './citations';
import { DeliverableCards } from './FileCard';
import { Markdown } from './Markdown';
import { turnFiles } from './turnFiles';
import { ProcessSummary } from './ProcessSummary';
import { QuestionCard, QuestionReplyBubble } from './QuestionCard';
import { ResultCards } from './ResultCards';
import { SearchConditions } from './SearchConditions';
import { TodoList } from './TodoList';
import { answerNotices } from '../events/notices';
import { splitAtQuestions } from '../events/segments';
import { hasDetails, selectRunCards, selectRunSearch } from '../events/selectors';
import type { FeedbackInput, Rating } from '../../../api/feedback';

interface Props {
  readonly run: RunView;
  readonly approvals: readonly ApprovalView[];
  readonly questions: readonly QuestionView[];
  /** 本会话引用目录；undefined 表示还没拉到。 */
  readonly sources?: Readonly<Record<number, CitationSource>>;
  readonly onDecide: (approvalId: string, allow: boolean, remember?: boolean) => Promise<void>;
  /** 审批卡片上给不给「本会话内同类操作不再询问」 */
  readonly rememberable?: boolean;
  /** 返回是否提交成功（失败已由页面提示）。 */
  readonly onAnswer: (questionId: string, answers: readonly QuestionAnswer[]) => Promise<boolean>;
  readonly onRegenerate?: () => void;
  /** 撤掉回答上方的一个检索条件、重新回答；交出去的是这一轮要去掉的全部条件（之前撤过的加这一个）。 */
  readonly onDropConditions?: (dropped: readonly string[]) => void;
  /** 打开这一轮的运行详情；没给就不显示入口。 */
  readonly onInspect?: () => void;
  /** 这一轮已记下的评分与评分、撤回的处理（见 AnswerActions）。 */
  readonly feedback?: Rating | null;
  readonly onFeedback?: (input: FeedbackInput) => Promise<boolean>;
  readonly onRetract?: () => Promise<boolean>;
  readonly busy: boolean;
  /** 在这个对话里问一句（结果卡片上接着问的建议点了用）；还在跑、或不能发问时不给 */
  readonly onAsk?: (text: string) => void;
  /** 正在逐字输出、还没成为正式消息的文字（events/live.ts），接在已有回答后面临时显示。 */
  readonly liveText?: string;
  /** 模型正在写的工具调用（还没开始跑）：执行过程那一行据此说正在做什么 */
  readonly liveTools?: readonly string[];
  /** 正在输出的那一步到目前为止的思考（events/live.ts），在执行过程那一行下面边想边显示 */
  readonly liveReasoning?: string;
}

/** 回答引用了、引用目录里却没有的编号；目录没拉到或回答没结束时不判断。 */
function invalidCitations(
  run: RunView,
  sources?: Readonly<Record<number, CitationSource>>,
): readonly number[] {
  if (!sources || !run.answer || run.endedAt === undefined) return [];
  return citedNumbers(run.answer).filter((n) => !sources[n]);
}

export function AssistantTurn({
  run,
  approvals,
  questions,
  sources,
  onDecide,
  rememberable = true,
  onAnswer,
  onRegenerate,
  onDropConditions,
  onInspect,
  busy,
  onAsk,
  liveText,
  liveTools,
  liveReasoning,
  feedback,
  onFeedback,
  onRetract,
}: Props) {
  // 和正式消息的拼法一致（onMessage 用空行分隔），正式消息到了整段替换时位置不跳
  const shown = liveText ? (run.answer ? `${run.answer}\n\n${liveText}` : liveText) : run.answer;
  const ended = run.endedAt !== undefined;
  const invalid = invalidCitations(run, sources);
  const notice = run.endReason && run.endReason.kind !== 'completed' ? run.endReason.message : undefined;
  const search = selectRunSearch(run);
  // 这一轮改过、交付的文件：正文里反引号点到它们的变成文件链接（照官方交付插件）
  const files = useMemo(() => turnFiles(run.steps, run.deliverables), [run.steps, run.deliverables]);
  const segments = useMemo(() => splitAtQuestions(run, questions), [run, questions]);
  const placed = new Set(segments.flatMap((segment) => (segment.question ? [segment.question.id] : [])));
  const asked = segments.at(-2)?.question;
  const progress = ended ? [] : (segments.at(-1)?.run.notes ?? []);
  // 最后一段还没东西、正在等上一个问题的回答：这一轮停在提问那里（同 ChatGPT），不再转一行「等待你回答」
  const waitingOnQuestion = asked?.status === 'pending' && !asked.continued;
  const answer = (question: QuestionView) =>
    question.status === 'answered' ? (
      <QuestionReplyBubble key={question.id} question={question} />
    ) : (
      <QuestionCard
        key={question.id}
        question={question}
        onAnswer={(answers) => onAnswer(question.id, answers)}
      />
    );
  return (
    <article className="kw-assistant" aria-label="智能体回答">
      {segments.map((segment, index) => {
        const last = index === segments.length - 1;
        const shown = last
          ? hasDetails(segment.run) || (!ended && !waitingOnQuestion)
          : hasDetails(segment.run);
        return (
          <Fragment key={segment.key}>
            {shown ? (
              <ProcessSummary
                run={segment.run}
                {...(last
                  ? {
                      preparing: liveTools,
                      thinking: liveReasoning || undefined,
                      answering: Boolean(liveText),
                    }
                  : {})}
              />
            ) : null}
            {index === 0 && run.todos ? <TodoList todos={run.todos} running={!ended} /> : null}
            {segment.question ? answer(segment.question) : null}
          </Fragment>
        );
      })}
      {/* 还在干活时，调工具之前写的那几句进度说明留在这里（照 ChatGPT 做长任务：干活时进度说明一直看得到，做完才和过程
          一起收进一行），正在写的那句接在后面、变成说明时位置不动，不再先冒出来又被收走 */}
      {progress.map((note, index) => (
        <Markdown key={`progress-${index}`} text={note} sources={sources} files={files} />
      ))}
      {approvals.map((approval) => (
        <ApprovalCard
          key={approval.id}
          approval={approval}
          step={run.steps.find((s) => s.callId === approval.callId)}
          rememberable={rememberable}
          onDecide={(allow, remember) => onDecide(approval.id, allow, remember)}
        />
      ))}
      {questions.filter((question) => !placed.has(question.id)).map(answer)}
      {search ? (
        <SearchConditions
          search={search}
          onDrop={
            onDropConditions && ended
              ? (condition) => onDropConditions([...search.removed, condition])
              : undefined
          }
        />
      ) : null}
      {shown ? <Markdown text={shown} sources={sources} streaming={Boolean(liveText)} files={files} /> : null}
      {invalid.length > 0 ? (
        <span className="kw-citation-notice">
          已去掉 {invalid.length} 处没有出处的引用编号：{invalid.map((n) => `[${n}]`).join(' ')}
        </span>
      ) : null}
      {/* 结果卡片等这一轮结束再出：模型先展示结果、后写结论，卡片早出的话，逐字输出的结论插在它上面、
          把它一路往下推（保持结论在上、卡片在下，内容只往下长）。过程里看步骤摘要 */}
      {ended ? (
        <ResultCards
          cards={selectRunCards(run)}
          question={run.asked[0]?.text}
          onAsk={busy ? undefined : onAsk}
        />
      ) : null}
      <DeliverableCards files={run.deliverables ?? []} />
      <AnswerNotices notices={answerNotices(run.steps)} />
      {notice ? <div className="kw-end-notice">{notice}</div> : null}
      {ended ? (
        <AnswerActions
          text={run.answer}
          onRegenerate={onRegenerate}
          regenerateDisabled={busy}
          onInspect={onInspect}
          feedback={feedback}
          onFeedback={onFeedback}
          onRetract={onRetract}
          // 引用目录拉到了才列：没拉到时不知道哪些编号有效
          trailing={sources ? <AnswerSources files={citedFiles(run.answer, sources)} /> : null}
        />
      ) : null}
    </article>
  );
}
