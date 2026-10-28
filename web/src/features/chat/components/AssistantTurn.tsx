/** 一轮助手回答：执行过程摘要、待办、审批与提问、正文（不套气泡）、文件、结束说明、操作图标。 */
import { useState } from 'react';
import type { QuestionAnswer } from '../../../api/conversations';
import type { ApprovalView, ArtifactView, CitationSource, QuestionView, RunView } from '../events/types';
import { AnswerActions } from './AnswerActions';
import { ApprovalCard } from './ApprovalCard';
import { AnswerSources } from './AnswerSources';
import { citedFiles, citedNumbers } from './citations';
import { FileCard } from './FileCard';
import { Markdown } from './Markdown';
import { ProcessSummary } from './ProcessSummary';
import { QuestionCard } from './QuestionCard';
import { SearchConditions } from './SearchConditions';
import { TodoList } from './TodoList';
import { hasProcess, selectRunSearch } from '../events/selectors';
import { FilePreview, type PreviewFile } from '../../preview/FilePreview';
import type { FeedbackInput, Rating } from '../../../api/feedback';

interface Props {
  readonly run: RunView;
  readonly approvals: readonly ApprovalView[];
  readonly questions: readonly QuestionView[];
  readonly artifacts: readonly ArtifactView[];
  /** 本会话引用目录；undefined 表示还没拉到。 */
  readonly sources?: Readonly<Record<number, CitationSource>>;
  readonly onDecide: (approvalId: string, allow: boolean, remember?: boolean) => Promise<void>;
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
  /** 正在逐字输出、还没成为正式消息的文字（events/live.ts），接在已有回答后面临时显示。 */
  readonly liveText?: string;
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
  artifacts,
  sources,
  onDecide,
  onAnswer,
  onRegenerate,
  onDropConditions,
  onInspect,
  busy,
  liveText,
  feedback,
  onFeedback,
  onRetract,
}: Props) {
  const [previewing, setPreviewing] = useState<PreviewFile | undefined>();
  // 和正式消息的拼法一致（onMessage 用空行分隔），正式消息到了整段替换时位置不跳
  const shown = liveText ? (run.answer ? `${run.answer}\n\n${liveText}` : liveText) : run.answer;
  const ended = run.endedAt !== undefined;
  const invalid = invalidCitations(run, sources);
  const notice = run.endReason && run.endReason.kind !== 'completed' ? run.endReason.message : undefined;
  const search = selectRunSearch(run);
  return (
    <article className="kw-assistant" aria-label="助手回答">
      {hasProcess(run) ? <ProcessSummary run={run} /> : null}
      {run.todos ? <TodoList todos={run.todos} running={!ended} /> : null}
      {approvals.map((approval) => (
        <ApprovalCard
          key={approval.id}
          approval={approval}
          step={run.steps.find((s) => s.callId === approval.callId)}
          onDecide={(allow, remember) => onDecide(approval.id, allow, remember)}
        />
      ))}
      {questions.map((question) => (
        <QuestionCard
          key={question.id}
          question={question}
          onAnswer={(answers) => onAnswer(question.id, answers)}
        />
      ))}
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
      {shown ? <Markdown text={shown} sources={sources} /> : null}
      {invalid.length > 0 ? (
        <span className="kw-citation-notice">
          已去掉 {invalid.length} 处没有出处的引用编号：{invalid.map((n) => `[${n}]`).join(' ')}
        </span>
      ) : null}
      {artifacts.map((file) => (
        <FileCard
          key={file.id}
          name={file.name}
          size={file.size}
          url={file.url}
          onPreview={file.url ? () => setPreviewing({ name: file.name, url: file.url! }) : undefined}
        />
      ))}
      <FilePreview file={previewing} onClose={() => setPreviewing(undefined)} />
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
