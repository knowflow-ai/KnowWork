/** 会话附件（server/src/knowwork_server/attachments/api.py）：上传后原件在沙箱 /workspace/input，发消息时带上 id。 */
import { ApiError, request, seg } from './http';
import { isRecord, listOf, num, str, text } from './parse';

export interface UploadedAttachment {
  readonly id: string;
  readonly filename: string;
  readonly size: number;
  readonly status: string;
}

export interface AttachmentFailure {
  readonly filename: string;
  readonly message: string;
}

/** 消息里显示的附件卡片：文件名与大小。 */
export interface AttachmentChip {
  readonly name: string;
  readonly size: number;
}

function parseItem(value: unknown): UploadedAttachment | undefined {
  if (!isRecord(value)) return undefined;
  const id = text(value.id);
  const filename = text(value.filename);
  if (!id || !filename) return undefined;
  return { id, filename, size: num(value.size) ?? 0, status: str(value.status) ?? '' };
}

function parseFailure(value: unknown): AttachmentFailure | undefined {
  if (!isRecord(value)) return undefined;
  return { filename: str(value.filename) ?? '', message: str(value.message) ?? '没有收下' };
}

export async function uploadAttachments(
  conversationId: string,
  files: readonly File[],
): Promise<{ items: readonly UploadedAttachment[]; failures: readonly AttachmentFailure[] }> {
  const form = new FormData();
  for (const file of files) form.append('files', file, file.name);
  const body = await request<unknown>(`/api/conversations/${seg(conversationId)}/attachments`, {
    method: 'POST',
    form,
  });
  const record = isRecord(body) ? body : {};
  return { items: listOf(record.items, parseItem), failures: listOf(record.failures, parseFailure) };
}

/**
 * 发消息前把这一轮的文件传上去，返回附件 id。有文件没收下就整轮不发（模型以为有、其实没有更糟），
 * 错误信息逐个说明哪个文件、为什么。
 */
export async function uploadForTurn(
  conversationId: string,
  files: readonly File[],
): Promise<readonly string[]> {
  if (files.length === 0) return [];
  const { items, failures } = await uploadAttachments(conversationId, files);
  const notReady = items.filter((item) => item.status !== 'ready');
  if (failures.length > 0 || notReady.length > 0) {
    const reasons = [
      ...failures.map((f) => `${f.filename}：${f.message}`),
      ...notReady.map((item) => `${item.filename}：还没放进工作区`),
    ];
    throw new ApiError(422, `附件没有全部收下，消息没有发出。${reasons.join('；')}`);
  }
  return items.map((item) => item.id);
}

export const chipsOf = (files: readonly File[]): readonly AttachmentChip[] =>
  files.map((file) => ({ name: file.name, size: file.size }));
