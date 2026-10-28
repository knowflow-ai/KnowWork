/**
 * 对话页的界面状态与事件流帧类型。
 *
 * 业务服务的 /events 推 events.v1（server/src/knowwork_server/conversations/events_v1.py）：
 * SSE id 是 `seq`（一帧投影出的最后一条）或 `seq.i`（同一帧的第 i 条），event 是事件类型，data 是事件 JSON。
 */
import type { PreviewLocate } from '../../preview/locate';
import type { AttachmentChip } from '../../../api/attachments';
import type { Mention } from '../../../api/mentions';

/** 事件在会话里的位置。index 为 Infinity 表示这一帧的最后一条（SSE id 不带小数部分）。 */
export interface Cursor {
  readonly seq: number;
  readonly index: number;
}

export type RunEventType = 'run.started' | 'run.completed' | 'run.failed' | 'run.cancelled' | 'run.waiting';
export type ItemEventType = 'item.started' | 'item.delta' | 'item.updated' | 'item.completed' | 'item.failed';

/** 一条 events.v1 事件（已校验过必需字段）。 */
export interface V1Event {
  readonly type: string;
  readonly seq: number;
  readonly runId: string | null;
  readonly time: number;
  readonly itemId: string | null;
  readonly kind: string | null;
  readonly data: Readonly<Record<string, unknown>>;
}

export interface WireEvent {
  /** 原样的 SSE id，断线续传时作为 Last-Event-ID 交回。 */
  readonly id: string;
  readonly cursor: Cursor;
  readonly event: V1Event;
}

export type EndReasonKind = 'completed' | 'failed' | 'cancelled' | 'max-tokens';

export interface EndReason {
  readonly kind: EndReasonKind;
  /** 只给用户看的说明，不含错误码。 */
  readonly message?: string;
}

export type StepStatus = 'running' | 'awaiting-approval' | 'done' | 'error' | 'rejected';

export interface StepView {
  readonly callId: string;
  readonly kind: 'tool' | 'subagent';
  readonly name: string;
  readonly arguments: string;
  readonly status: StepStatus;
  readonly startedAt?: number;
  readonly endedAt?: number;
  /** 工具结果的文本预览（截断）。 */
  readonly resultPreview?: string;
  readonly errorReason?: string;
  /** 知识检索的摘要（业务服务按完整结果摘出）：回答上方据此列出检索条件。 */
  readonly search?: SearchSummary;
}

/** 一次知识检索：命中的文档（来源:文档，去重）、用上的条件、用户这一轮撤掉的条件。条件的写法由业务服务给出。 */
export interface SearchSummary {
  readonly documents: readonly string[];
  readonly conditions: readonly string[];
  readonly removed: readonly string[];
}

export type RunStatus = 'running' | 'waiting' | 'completed' | 'failed' | 'cancelled';

export interface RunView {
  readonly runId: string;
  readonly status: RunStatus;
  readonly startedAt?: number;
  readonly endedAt?: number;
  readonly endReason?: EndReason;
  readonly steps: readonly StepView[];
  /** 执行过程中的说明文字（工具调用之前的那几段模型输出）。 */
  readonly notes: readonly string[];
  /** 模型的思考过程，只在展开执行过程时显示。 */
  readonly thinking: readonly string[];
  /** 回答正文（最后一次工具调用之后的模型输出）。 */
  readonly answer: string;
  /** 执行中出现的问题（已翻译成可读的话）。 */
  readonly problems: readonly string[];
  /** 这一轮里用户发的消息（提问与插话），来自事件流，刷新后仍在。 */
  readonly asked: readonly AskedMessage[];
  /** 执行过程按发生先后排的条目：思考、说明与步骤交错，展开执行过程时按这个顺序显示。 */
  readonly trail: readonly TrailEntry[];
  /** 这一轮 Agent 维护的待办（官方 todo_write 的最新整表）；没写过时为 undefined。 */
  readonly todos?: readonly TodoView[];
}

export type TodoStatus = 'pending' | 'in_progress' | 'completed';

export interface TodoView {
  readonly content: string;
  readonly status: TodoStatus;
}

export type TrailEntry =
  | { readonly type: 'thinking'; readonly text: string }
  | { readonly type: 'note'; readonly text: string }
  | { readonly type: 'step'; readonly callId: string };

export interface AskedMessage {
  readonly id: string;
  readonly text: string;
  /** 这条提问带的文件（事件流里宿主写的附件说明，服务端已换成名字与大小）。 */
  readonly attachments?: readonly AttachmentChip[];
  /** 这条提问 @ 的范围与技能（宿主写的 @ 说明块，服务端已取出来）；目录只有路径，重新生成时再展开。 */
  readonly mentions?: readonly Mention[];
}

export type ApprovalStatus = 'pending' | 'allowed' | 'rejected' | 'cancelled';

export interface ApprovalView {
  readonly id: string;
  readonly toolName: string;
  readonly callId?: string;
  readonly reason?: string;
  readonly runId?: string;
  readonly status: ApprovalStatus;
}

export interface QuestionOptionView {
  readonly label: string;
  readonly description?: string;
}

/** 一个问题（官方 ask_user_question 的词汇）。 */
export interface QuestionItemView {
  readonly id: string;
  readonly question: string;
  readonly header?: string;
  readonly detail?: string;
  readonly options: readonly QuestionOptionView[];
  readonly multiSelect: boolean;
}

/** 一题的回答：selected 是选项原标签（回传给后端要一字不差）；单选题填了 custom 时 selected 为空。 */
export interface QuestionAnswerView {
  readonly id: string;
  readonly selected: readonly string[];
  readonly custom?: string;
}

/**
 * 关闭原因：timeout 等待超时；cancelled 回合取消；interrupted 运行时正常重启时官方把等待中止了；
 * expired 本页提交时后端说问题已结束。四种都不能再在卡片上回答。
 */
export type QuestionClosedReason = 'timeout' | 'cancelled' | 'interrupted' | 'expired';

/** Agent 的一次提问（一批问题），id 是工具调用 id；runId 是提问时的那一轮。 */
export interface QuestionView {
  readonly id: string;
  readonly runId?: string;
  readonly questions: readonly QuestionItemView[];
  readonly status: 'pending' | 'answered' | 'closed';
  /** 前台等待已结束，Agent 先继续了；回答后会开新一轮接着处理。 */
  readonly continued: boolean;
  readonly answers?: readonly QuestionAnswerView[];
  readonly closedReason?: QuestionClosedReason;
}

/** 会话产物（回合结束时从沙箱 /workspace/output 收取），runId 与 events.v1 的 run_id 一致。 */
export interface ArtifactView {
  readonly id: string;
  readonly name: string;
  readonly mimeType?: string;
  readonly size?: number;
  readonly url?: string;
  readonly runId?: string;
}

/** 回答里 [n] 的出处：来自 /citations，按编号查。 */
/** 片段里的图：回答里的 [IMG:n:j] 换成第 n 条出处的第 j 张 */
export interface CitationImage {
  /** 站内取图地址（带签名）或 http(s) 地址 */
  readonly url: string;
  readonly caption: string;
}

export interface CitationSource {
  readonly title: string;
  readonly excerpt?: string;
  readonly page?: number;
  readonly url?: string;
  /** 哪个知识源、哪份文档：有引用预览器（插槽 citation.preview）时据此打开预览 */
  readonly sourceId?: string;
  readonly docRef?: string;
  /** 点开时高亮哪里：这一块的几处位置合起来（整块） */
  readonly locate?: PreviewLocate;
  /** 这一块里每处的位置与原文：回答里原样写了某处原文时只标那几处（components/claim.ts） */
  readonly spots?: readonly { readonly locate: PreviewLocate; readonly quote: string }[];
  readonly images?: readonly CitationImage[];
  /** 知识源给的版本提示（例如「可能已有新版《…》」），卡片上照原样显示 */
  readonly versionNote?: string;
}

/**
 * 本页发出的用户消息。events.v1 目前不含用户消息，只能在本页记住；
 * afterRuns 记下发送时已有几轮，用来在时间线里摆到正确位置（重算时也稳定）。
 */
export interface SentMessage {
  readonly localId: string;
  readonly text: string;
  readonly mode: 'followup' | 'steer';
  readonly afterRuns: number;
  readonly status: 'sending' | 'sent';
  readonly attachments?: readonly AttachmentChip[];
  readonly mentions?: readonly Mention[];
}

export type TimelineEntry =
  { readonly type: 'user'; readonly message: SentMessage } | { readonly type: 'run'; readonly runId: string };

export type RunState = 'idle' | 'running' | 'waiting-approval';

export interface ChatState {
  /** 已应用的帧，按位置升序；乱序到达时据此整体重算。 */
  readonly log: readonly WireEvent[];
  /** 已应用的最后位置与它的 SSE id，断线续传从这里接。 */
  readonly last?: Cursor;
  readonly lastId?: string;
  readonly runOrder: readonly string[];
  readonly runs: Readonly<Record<string, RunView>>;
  readonly approvals: readonly ApprovalView[];
  readonly questions: readonly QuestionView[];
  /** 不作为步骤显示的工具调用（提问、待办由卡片与清单展示），它们的结果也不再生成步骤。 */
  readonly internalCalls: readonly string[];
  /** 本页提交回答时后端说已结束的提问；不来自事件流，重算时保留。 */
  readonly expiredQuestions: readonly string[];
  readonly artifacts: readonly ArtifactView[];
  /** 本会话引用目录，按编号查出处；undefined 表示还没拉到（拉到之前不判断编号是否有效）。 */
  readonly citations?: Readonly<Record<number, CitationSource>>;
  /** 当前未结束的轮次。 */
  readonly openRun?: string;
  readonly sent: readonly SentMessage[];
  /** 正在逐字输出的那一步（events/live.ts）：片段拼成，不进事件日志，这一步的正式消息到了就清掉。 */
  readonly live?: LiveText;
}

export interface LiveText {
  readonly runId: string;
  readonly step: number;
  readonly text: string;
}

/** 服务端推来的一个逐字片段：offset 为 0 表示从头替换；done 表示这一步输出结束。 */
export type LivePiece =
  | { readonly runId: string; readonly step: number; readonly offset: number; readonly text: string }
  | { readonly runId: string; readonly step: number; readonly done: true };

export type ChatAction =
  | { readonly type: 'wire'; readonly event: WireEvent }
  | { readonly type: 'wire/batch'; readonly events: readonly WireEvent[] }
  | {
      readonly type: 'local/sent';
      readonly localId: string;
      readonly text: string;
      readonly mode: SentMessage['mode'];
      readonly attachments?: readonly AttachmentChip[];
      readonly mentions?: readonly Mention[];
    }
  | { readonly type: 'local/delivered'; readonly localId: string }
  | { readonly type: 'local/failed'; readonly localId: string }
  /** 提交回答时后端说问题已结束（宿主重启过等），把卡片标成失效。 */
  | { readonly type: 'local/question-expired'; readonly questionId: string }
  /** 从 /artifacts 拉到的本会话产物，整体替换。 */
  | { readonly type: 'artifacts/loaded'; readonly artifacts: readonly ArtifactView[] }
  /** 从 /citations 拉到的本会话引用目录，整体替换。 */
  | { readonly type: 'citations/loaded'; readonly citations: Readonly<Record<number, CitationSource>> }
  /** 逐字片段（不来自事件日志）。 */
  | { readonly type: 'live'; readonly live: LivePiece }
  | { readonly type: 'reset' };
