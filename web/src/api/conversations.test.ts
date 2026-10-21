import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  cancelRun,
  createConversation,
  decideApproval,
  deleteConversation,
  eventsUrl,
  listConversations,
  MAX_MESSAGE_LENGTH,
  renameConversation,
  sendMessage,
  validateMessage,
  validateTitle,
} from './conversations';
import { jsonResponse, routeFetch } from '../test/render';

afterEach(() => vi.unstubAllGlobals());

describe('conversations api', () => {
  it('校验消息与名称', () => {
    expect(validateMessage('  ')).toBe('请输入内容');
    expect(validateMessage('x'.repeat(MAX_MESSAGE_LENGTH + 1))).toBe('内容太长，请精简到 20 万字以内');
    expect(validateMessage('你好')).toBeNull();
    expect(validateTitle(' ')).toBe('请输入名称');
    expect(validateTitle('x'.repeat(201))).toBe('名称不能超过 200 个字');
    expect(validateTitle('周报')).toBeNull();
  });

  it('按接口约定调用', async () => {
    const api = routeFetch({
      'POST /api/conversations': jsonResponse({ id: 'c1', title: 't' }, 201),
      'POST /api/conversations/*/messages': jsonResponse({ messageId: 'm' }, 202),
      'POST /api/conversations/*/cancel': jsonResponse({ settled: true }),
      'POST /api/conversations/*/approvals/*': jsonResponse({}),
      'PATCH /api/conversations/*': jsonResponse({}),
      'DELETE /api/conversations/*': new Response(null, { status: 204 }),
    });
    await expect(createConversation('标'.repeat(300))).resolves.toEqual({ id: 'c1', title: 't' });
    await sendMessage('a/b', 'hi', 'steer');
    await cancelRun('c1');
    await decideApproval('c1', 'ap 1', true);
    await renameConversation('c1', '  新名  ');
    await deleteConversation('c1');
    const [create, send, cancel, decide, rename, remove] = api.calls;
    expect((create?.body as { title: string }).title).toHaveLength(200);
    expect(send).toMatchObject({
      path: '/api/conversations/a%2Fb/messages',
      body: { text: 'hi', mode: 'steer' },
    });
    expect(cancel?.path).toBe('/api/conversations/c1/cancel');
    expect(decide).toMatchObject({ path: '/api/conversations/c1/approvals/ap%201', body: { allow: true } });
    expect(rename).toMatchObject({ method: 'PATCH', body: { title: '新名' } });
    expect(remove?.method).toBe('DELETE');
    expect(eventsUrl('c 1')).toBe('/api/conversations/c%201/events');
  });

  it('列表分页：带游标、解析下一页，坏数据丢弃', async () => {
    const api = routeFetch({
      'GET /api/conversations': jsonResponse({
        items: [
          { id: 'c1', title: '周报', updatedAt: '2026-10-19T00:00:00+00:00' },
          { title: '没有 id' },
          'x',
        ],
        next: '2026-10-18T00:00:00+00:00',
      }),
    });
    const page = await listConversations('2026-10-20T00:00:00+00:00');
    expect(page.items).toEqual([
      { id: 'c1', title: '周报', createdAt: null, updatedAt: '2026-10-19T00:00:00+00:00' },
    ]);
    expect(page.next).toBe('2026-10-18T00:00:00+00:00');
    expect(api.calls[0]?.query.get('before')).toBe('2026-10-20T00:00:00+00:00');
    expect(api.calls[0]?.query.get('limit')).toBe('30');
  });

  it('创建会话返回不完整时报错', async () => {
    routeFetch({ 'POST /api/conversations': jsonResponse({}, 201) });
    await expect(createConversation('x')).rejects.toThrow('服务返回的数据不完整');
    routeFetch({ 'GET /api/conversations': jsonResponse('bad') });
    await expect(listConversations()).resolves.toEqual({ items: [], next: null });
  });
});
