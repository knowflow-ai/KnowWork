import { afterEach, describe, expect, it, vi } from 'vitest';
import { artifactUrl, listArtifacts } from './artifacts';
import { jsonResponse, routeFetch } from '../test/render';

afterEach(() => vi.unstubAllGlobals());

describe('artifacts api', () => {
  it('列出会话产物：字段收窄成界面用的样子，下载地址走鉴权接口', async () => {
    const api = routeFetch({
      'GET /api/conversations/*/artifacts': jsonResponse({
        items: [
          { id: 'art_1', filename: '总结.docx', contentType: 'application/x', size: 2048, runId: 'run-2' },
          { id: 'art_2', filename: '无轮次.md', contentType: 'text/markdown', size: 3, runId: null },
          { filename: '缺 id' },
          'not-an-object',
        ],
      }),
    });
    const items = await listArtifacts('a/b');
    expect(api.calls[0]?.path).toBe('/api/conversations/a%2Fb/artifacts');
    expect(items).toEqual([
      {
        id: 'art_1',
        name: '总结.docx',
        mimeType: 'application/x',
        size: 2048,
        runId: 'run-2',
        url: '/api/conversations/a%2Fb/artifacts/art_1',
      },
      {
        id: 'art_2',
        name: '无轮次.md',
        mimeType: 'text/markdown',
        size: 3,
        runId: undefined,
        url: '/api/conversations/a%2Fb/artifacts/art_2',
      },
    ]);
    expect(artifactUrl('c1', 'x')).toBe('/api/conversations/c1/artifacts/x');
  });

  it('响应不是预期结构时当作没有产物', async () => {
    routeFetch({ 'GET /api/conversations/*/artifacts': jsonResponse({ nope: true }) });
    await expect(listArtifacts('c1')).resolves.toEqual([]);
  });
});
