/**
 * 会话产物接口（server/src/knowwork_server/artifacts/api.py）。
 *
 * 回合结束时后端把模型用 present 交付的文件收成产物（连同交付时写的说明），runId 与 events.v1 的 run_id 同为 `run-<轮次>`。
 * 下载走同源鉴权接口，浏览器自动带 Cookie；后端一律按附件返回。
 */
import type { ArtifactView } from '../features/chat/events/types';
import { request, seg } from './http';
import { isRecord, listOf, num, str, text } from './parse';
import { workspacePath } from './workspace';

export function artifactUrl(conversationId: string, artifactId: string): string {
  return workspacePath(`/conversations/${seg(conversationId)}/artifacts/${seg(artifactId)}`);
}

function parseArtifact(conversationId: string, value: unknown): ArtifactView | undefined {
  if (!isRecord(value)) return undefined;
  const id = text(value.id);
  const name = text(value.filename);
  if (!id || !name) return undefined;
  return {
    id,
    name,
    mimeType: str(value.contentType),
    size: num(value.size),
    runId: text(value.runId),
    description: text(value.description),
    url: artifactUrl(conversationId, id),
  };
}

export async function listArtifacts(
  conversationId: string,
  signal?: AbortSignal,
): Promise<readonly ArtifactView[]> {
  const body = await request<unknown>(workspacePath(`/conversations/${seg(conversationId)}/artifacts`), {
    signal,
  });
  const items = isRecord(body) ? body.items : undefined;
  return listOf(items, (item) => parseArtifact(conversationId, item));
}
