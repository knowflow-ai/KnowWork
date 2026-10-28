import { afterEach, describe, expect, it, vi } from 'vitest';
import { deleteFeedback, listFeedback, putFeedback } from './feedback';
import { jsonResponse, routeFetch } from '../test/render';

afterEach(() => vi.unstubAllGlobals());

describe('feedback api', () => {
  it('列出这个会话每轮的评分，认不出的丢掉；提交时去掉说明两头的空白，撤回走 DELETE', async () => {
    const api = routeFetch({
      'GET /api/workspaces/default/conversations/*/feedback': jsonResponse({
        items: [
          { runId: 'run-1', rating: 'like', category: null, note: null },
          { runId: 'run-2', rating: 'dislike', category: 'task-result', note: '不对' },
          { runId: 'run-3', rating: 'meh' },
        ],
      }),
      'PUT /api/workspaces/default/conversations/*/feedback/*': jsonResponse({}),
      'DELETE /api/workspaces/default/conversations/*/feedback/*': jsonResponse({}),
    });
    expect(await listFeedback('c1')).toEqual([
      { runId: 'run-1', rating: 'like', category: null, note: null },
      { runId: 'run-2', rating: 'dislike', category: 'task-result', note: '不对' },
    ]);
    await putFeedback('c1', 'run-2', { rating: 'dislike', category: 'other', note: '  太慢 ' });
    expect(api.find('PUT', '/api/workspaces/default/conversations/c1/feedback/run-2')[0]?.body).toEqual({
      rating: 'dislike',
      category: 'other',
      note: '太慢',
    });
    await deleteFeedback('c1', 'run-2');
    expect(api.find('DELETE', '/api/workspaces/default/conversations/c1/feedback/run-2')).toHaveLength(1);
  });
});
