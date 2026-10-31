/**
 * 运行调试视图（server/src/knowwork_server/debugview）：一轮的步骤时间线、每步按日志还原的输入、原始日志导出；
 * 管理端的「运行记录」列表。会话本人走 /api/workspaces/…/conversations/…/debug，管理员走 /api/workspaces/…/runs（会记审计）。
 */
import { request, seg } from './http';
import { bool, isRecord, listOf, num, str, strings } from './parse';
import { workspacePath } from './workspace';

/** 从哪里取一轮的运行详情：会话本人，或管理员看全空间的。 */
export interface RunSource {
  readonly key: readonly string[];
  readonly turnPath: (turn: number) => string;
}

export const ownRun = (conversationId: string): RunSource => ({
  key: ['debug', conversationId],
  turnPath: (turn) => workspacePath(`/conversations/${seg(conversationId)}/debug/turns/${turn}`),
});

export const workspaceRun = (conversationId: string): RunSource => ({
  key: ['admin', 'runs', conversationId],
  turnPath: (turn) => workspacePath(`/runs/${seg(conversationId)}/turns/${turn}`),
});

export interface DebugUsage {
  readonly model: string;
  readonly inputTokens: number;
  readonly outputTokens: number;
  readonly cost: string | null;
  readonly durationMs: number | null;
}

export interface DebugToolCall {
  readonly callId: string;
  readonly name: string;
  readonly arguments: string;
  readonly result: string | null;
  readonly isError: boolean;
  readonly durationMs: number | null;
  readonly truncated: boolean;
  /** 业务另记的过程（知识检索的各路召回、重排……），模型没看到 */
  readonly trace: readonly DebugTrace[];
}

/** 过程里的一项：哪一路或哪一步、一句结论、几行明细，哪个知识源。 */
export interface DebugTrace {
  readonly label: string;
  readonly summary: string;
  readonly lines: readonly string[];
  readonly source: string;
}

export interface DebugStep {
  readonly step: number;
  readonly durationMs: number | null;
  readonly reasoning: string;
  readonly text: string;
  readonly toolCalls: readonly DebugToolCall[];
  readonly failures: readonly string[];
  readonly usage: DebugUsage | null;
}

export interface TurnSummary {
  readonly turn: number;
  readonly startedMs: number | null;
  readonly durationMs: number | null;
  /** completed、cancelled、failed……；还没结束是 running。 */
  readonly status: string;
  readonly request: string;
  readonly steps: number;
  readonly toolCalls: number;
  readonly errors: number;
  readonly inputTokens: number;
  readonly outputTokens: number;
  readonly cost: string | null;
}

export interface DebugSubagent {
  readonly sessionId: string;
  readonly label: string;
  readonly steps: readonly DebugStep[];
}

export interface TurnDetail {
  readonly summary: TurnSummary;
  readonly steps: readonly DebugStep[];
  readonly subagents: readonly DebugSubagent[];
  /** 技能加载、审批、提问这类不属于某一步的事。 */
  readonly events: readonly string[];
  readonly background: readonly DebugBackground[];
}

/**
 * 随提问带上的背景：扩展每轮补的上下文（例如记忆）。standing 每轮都带，只在变了时才发，
 * 沿用之前那一份时 sentTurn 早于这一轮；turn 只给这一轮。
 */
export interface DebugBackground {
  readonly kind: 'standing' | 'turn';
  readonly text: string;
  readonly sentTurn: number;
}

export interface DebugBlock {
  readonly type: string;
  readonly text: string;
  readonly name: string | null;
  readonly truncated: boolean;
}

export interface DebugMessage {
  readonly role: string;
  /** user 是用户亲手发的；runtime-context、skill-invocation 等是宿主塞进去的上下文。 */
  readonly source: string | null;
  readonly blocks: readonly DebugBlock[];
  readonly toolCallId: string | null;
  readonly isError: boolean;
}

export interface StepInput {
  readonly turn: number;
  readonly step: number;
  readonly system: readonly string[];
  readonly tools: readonly { readonly name: string; readonly description: string }[];
  readonly messages: readonly DebugMessage[];
  readonly notes: readonly string[];
}

const nullable = (value: unknown) => num(value) ?? null;
const textOrNull = (value: unknown) => str(value) ?? null;

function parseUsage(value: unknown): DebugUsage | null {
  if (!isRecord(value)) return null;
  return {
    model: str(value.model) ?? '',
    inputTokens: num(value.inputTokens) ?? 0,
    outputTokens: num(value.outputTokens) ?? 0,
    cost: textOrNull(value.cost),
    durationMs: nullable(value.durationMs),
  };
}

function parseCall(value: unknown): DebugToolCall | undefined {
  if (!isRecord(value)) return undefined;
  return {
    callId: str(value.callId) ?? '',
    name: str(value.name) ?? '',
    arguments: str(value.arguments) ?? '',
    result: textOrNull(value.result),
    isError: bool(value.isError),
    durationMs: nullable(value.durationMs),
    truncated: bool(value.truncated),
    trace: listOf(value.trace, parseTrace),
  };
}

function parseTrace(value: unknown): DebugTrace | undefined {
  if (!isRecord(value) || !str(value.label)) return undefined;
  return {
    label: str(value.label) ?? '',
    summary: str(value.summary) ?? '',
    lines: strings(value.lines),
    source: str(value.source) ?? '',
  };
}

function parseStep(value: unknown): DebugStep | undefined {
  if (!isRecord(value) || num(value.step) === undefined) return undefined;
  return {
    step: num(value.step) ?? 0,
    durationMs: nullable(value.durationMs),
    reasoning: str(value.reasoning) ?? '',
    text: str(value.text) ?? '',
    toolCalls: listOf(value.toolCalls, parseCall),
    failures: strings(value.failures),
    usage: parseUsage(value.usage),
  };
}

function parseSummary(value: unknown): TurnSummary | undefined {
  if (!isRecord(value) || num(value.turn) === undefined) return undefined;
  return {
    turn: num(value.turn) ?? 0,
    startedMs: nullable(value.startedMs),
    durationMs: nullable(value.durationMs),
    status: str(value.status) ?? 'ended',
    request: str(value.request) ?? '',
    steps: num(value.steps) ?? 0,
    toolCalls: num(value.toolCalls) ?? 0,
    errors: num(value.errors) ?? 0,
    inputTokens: num(value.inputTokens) ?? 0,
    outputTokens: num(value.outputTokens) ?? 0,
    cost: textOrNull(value.cost),
  };
}

function parseBackground(value: unknown): DebugBackground | undefined {
  if (!isRecord(value)) return undefined;
  const kind = value.kind === 'standing' || value.kind === 'turn' ? value.kind : undefined;
  const text = str(value.text);
  const sentTurn = num(value.sentTurn);
  return kind && text && sentTurn !== undefined ? { kind, text, sentTurn } : undefined;
}

function parseBlock(value: unknown): DebugBlock | undefined {
  if (!isRecord(value)) return undefined;
  return {
    type: str(value.type) ?? 'text',
    text: str(value.text) ?? '',
    name: textOrNull(value.name),
    truncated: bool(value.truncated),
  };
}

function parseMessage(value: unknown): DebugMessage | undefined {
  if (!isRecord(value)) return undefined;
  return {
    role: str(value.role) ?? 'unknown',
    source: textOrNull(value.source),
    blocks: listOf(value.blocks, parseBlock),
    toolCallId: textOrNull(value.toolCallId),
    isError: bool(value.isError),
  };
}

export async function getTurn(source: RunSource, turn: number): Promise<TurnDetail> {
  const body = await request<unknown>(source.turnPath(turn));
  const value = isRecord(body) ? body : {};
  const summary = parseSummary(value.summary);
  if (!summary) throw new Error('运行记录不完整');
  return {
    summary,
    steps: listOf(value.steps, parseStep),
    subagents: listOf(value.subagents, (item) =>
      isRecord(item)
        ? {
            sessionId: str(item.sessionId) ?? '',
            label: str(item.label) ?? '',
            steps: listOf(item.steps, parseStep),
          }
        : undefined,
    ),
    events: strings(value.events),
    background: listOf(value.background, parseBackground),
  };
}

export async function getStepInput(source: RunSource, turn: number, step: number): Promise<StepInput> {
  const body = await request<unknown>(`${source.turnPath(turn)}/steps/${step}/input`);
  const value = isRecord(body) ? body : {};
  return {
    turn,
    step,
    system: strings(value.system),
    tools: listOf(value.tools, (item) =>
      isRecord(item) ? { name: str(item.name) ?? '', description: str(item.description) ?? '' } : undefined,
    ),
    messages: listOf(value.messages, parseMessage),
    notes: strings(value.notes),
  };
}

/** 这一轮原始日志的下载地址（同源 Cookie 鉴权，直接用作链接）。 */
export function exportUrl(source: RunSource, turn: number): string {
  return `${source.turnPath(turn)}/export`;
}

/** events.v1 的 run_id 是 run-<轮次>。 */
export function turnOfRun(runId: string): number | undefined {
  const match = /^run-(\d+)$/.exec(runId);
  return match ? Number(match[1]) : undefined;
}

// ---- 管理端「运行记录」 ----

export interface RunRow {
  readonly conversationId: string;
  readonly title: string;
  readonly userId: string;
  readonly userName: string | null;
  readonly turn: number;
  readonly startedAt: string;
  readonly status: string;
  readonly durationMs: number | null;
  readonly modelCalls: number;
  readonly inputTokens: number;
  readonly outputTokens: number;
  readonly cost: string | null;
  /** 用户这一轮亲自发的第一条消息 */
  readonly question: string;
  /** 这一轮调过的工具，按第一次调用的先后 */
  readonly tools: readonly string[];
  /** 检索知识库几次、回答引用了几条；没检索过都为空 */
  readonly searches: number | null;
  readonly cited: number | null;
  readonly rating: 'like' | 'dislike' | null;
}

/** 运行记录的信号筛选：检索了知识库却没引用（答完的才算）、被点了踩、没答上来（回合结束后模型判断知识库没答上或
 *  只答了一部分，每小时一批，刚答完的还没判） */
export const RUN_SIGNALS = [
  { value: 'uncited', label: '检索了没引用' },
  { value: 'disliked', label: '被点踩' },
  { value: 'unanswered', label: '没答上来' },
] as const;

export interface RunFilter {
  readonly since?: string;
  readonly until?: string;
  readonly user?: string;
  readonly status?: string;
  readonly signal?: string;
  /** 用过这个工具 */
  readonly tool?: string;
  /** 检索到这个知识库（全局 id） */
  readonly base?: string;
}

function parseRow(value: unknown): RunRow | undefined {
  if (!isRecord(value)) return undefined;
  const conversationId = str(value.conversationId);
  const turn = num(value.turn);
  if (!conversationId || turn === undefined) return undefined;
  return {
    conversationId,
    title: str(value.title) ?? '',
    userId: str(value.userId) ?? '',
    userName: textOrNull(value.userName),
    turn,
    startedAt: str(value.startedAt) ?? '',
    status: str(value.status) ?? 'ended',
    durationMs: nullable(value.durationMs),
    modelCalls: num(value.modelCalls) ?? 0,
    inputTokens: num(value.inputTokens) ?? 0,
    outputTokens: num(value.outputTokens) ?? 0,
    cost: textOrNull(value.cost),
    question: str(value.question) ?? '',
    tools: strings(value.tools),
    searches: nullable(value.searches),
    cited: nullable(value.cited),
    rating: value.rating === 'like' || value.rating === 'dislike' ? value.rating : null,
  };
}

export async function listRuns(
  filter: RunFilter,
  before: string | null,
): Promise<{ readonly items: readonly RunRow[]; readonly next: string | null }> {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filter)) if (value) params.set(key, value);
  if (before) params.set('before', before);
  const query = params.toString();
  const body = await request<unknown>(workspacePath(`/runs${query ? `?${query}` : ''}`));
  const value = isRecord(body) ? body : {};
  return { items: listOf(value.items, parseRow), next: textOrNull(value.next) };
}
