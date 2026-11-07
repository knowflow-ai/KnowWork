/**
 * 会话附件（server/src/knowwork_server/attachments/api.py）。文件先有、对话后建：输入框里一选文件就传上来暂存（还没有
 * 会话也行），服务端马上开始解析；发消息时带上附件 id，那时才放进会话的沙箱 /workspace/input。
 */
import { ApiError, request, seg } from './http';
import { isRecord, listOf, num, str, text } from './parse';
import { workspacePath } from './workspace';

/** 文档解析（PDF、Word、PPT、图片经 MinerU 转 Markdown）到哪一步了；none 是这个文件不解析。 */
export type ParseState = 'parsing' | 'ready' | 'failed' | 'none';

/** 导读（放不进对话的长文档，后台按段摘要、汇总成全文摘要）到哪一步了；none 是不做。 */
export type DigestState = 'digesting' | 'ready' | 'failed' | 'none';

export interface StagedAttachment {
  readonly id: string;
  readonly filename: string;
  readonly size: number;
  readonly status: string;
  readonly parse: ParseState;
  readonly digest: DigestState;
  /** 导读做到第几段、一共几段（还没切好段时没有） */
  readonly digestProgress?: readonly [number, number];
}

/** 消息里显示的附件卡片：文件名与大小；输入框里等上传、解析时另带一句状态。 */
export interface AttachmentChip {
  readonly name: string;
  readonly size: number;
  readonly note?: string;
  /** 状态还在变（上传中、解析中）：状态前转一个小圈 */
  readonly busy?: boolean;
}

/** 随消息发出的附件：id 交给服务端，名字与大小给消息上的卡片。 */
export interface SentAttachment {
  readonly id: string;
  readonly name: string;
  readonly size: number;
}

/** 解析进度多久查一次。解析由后台任务做，PDF 动辄几秒到几分钟，查得太勤没有意义 */
export const PARSE_POLL_MS = 1500;

const BASE = '/attachments';

function parseStateOf(value: unknown): ParseState {
  return value === 'parsing' || value === 'ready' || value === 'failed' ? value : 'none';
}

function digestStateOf(value: unknown): DigestState {
  return value === 'digesting' || value === 'ready' || value === 'failed' ? value : 'none';
}

function parseItem(value: unknown): StagedAttachment | undefined {
  if (!isRecord(value)) return undefined;
  const id = text(value.id);
  const filename = text(value.filename);
  if (!id || !filename) return undefined;
  const done = num(value.digestDone);
  const total = num(value.digestTotal);
  return {
    id,
    filename,
    size: num(value.size) ?? 0,
    status: str(value.status) ?? '',
    parse: parseStateOf(value.parseStatus),
    digest: digestStateOf(value.digestStatus),
    ...(done !== undefined && total ? { digestProgress: [done, total] as const } : {}),
  };
}

/** 传一个文件上来暂存；没收下（格式不支持、太大等）时抛出带原因的错误，原因可以直接给用户看。 */
export async function stageAttachment(file: File): Promise<StagedAttachment> {
  const form = new FormData();
  form.append('files', file, file.name);
  const body = await request<unknown>(workspacePath(BASE), { method: 'POST', form });
  const record = isRecord(body) ? body : {};
  const [item] = listOf(record.items, parseItem);
  if (item) return item;
  const [failure] = listOf(record.failures, (value) => (isRecord(value) ? str(value.message) : undefined));
  throw new ApiError(422, `${file.name}：${failure ?? '没有收下'}`);
}

/** 本人这几个附件现在怎样（等解析时查）；别人的、已删的不返回。 */
export async function attachmentStates(ids: readonly string[]): Promise<readonly StagedAttachment[]> {
  if (ids.length === 0) return [];
  const query = new URLSearchParams({ ids: ids.join(',') });
  const body = await request<unknown>(`${workspacePath(BASE)}?${query.toString()}`);
  return listOf(isRecord(body) ? body.items : undefined, parseItem);
}

/** 输入框里移除了一个还没发出去的附件。 */
export async function discardAttachment(id: string): Promise<void> {
  await request<unknown>(workspacePath(`${BASE}/${seg(id)}`), { method: 'DELETE' });
}

export const chipsOf = (attachments: readonly { name: string; size: number }[]): readonly AttachmentChip[] =>
  attachments.map(({ name, size }) => ({ name, size }));
