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
      'POST /api/workspaces/default/conversations': jsonResponse({ id: 'c1', title: 't' }, 201),
      'POST /api/workspaces/default/conversations/*/messages': jsonResponse({ messageId: 'm' }, 202),
      'POST /api/workspaces/default/conversations/*/cancel': jsonResponse({ settled: true }),
      'POST /api/workspaces/default/conversations/*/approvals/*': jsonResponse({}),
      'PATCH /api/workspaces/default/conversations/*': jsonResponse({}),
      'DELETE /api/workspaces/default/conversations/*': new Response(null, { status: 204 }),
    });
    await expect(createConversation('标'.repeat(300))).resolves.toEqual({ id: 'c1', title: 't' });
    await sendMessage('a/b', 'hi', 'steer');
    await sendMessage('c1', '再答', 'followup', {
      answerMode: 'quick',
      droppedConditions: ['部门 = 财务部'],
      mentions: [
        { type: 'document', id: 'kb1/d1', name: '差旅办法.pdf', base: 'builtin.kb1', baseName: '制度库' },
      ],
    });
    await cancelRun('c1');
    await decideApproval('c1', 'ap 1', true);
    await renameConversation('c1', '  新名  ');
    await deleteConversation('c1');
    const [create, send, resend, cancel, decide, rename, remove] = api.calls;
    expect((create?.body as { title: string }).title).toHaveLength(200);
    expect(send).toMatchObject({
      path: '/api/workspaces/default/conversations/a%2Fb/messages',
      body: { text: 'hi', mode: 'steer' },
    });
    expect(send?.body).not.toHaveProperty('dropped_conditions');
    expect(send?.body).not.toHaveProperty('answer_mode');
    expect(resend?.body).toEqual({
      text: '再答',
      mode: 'followup',
      answer_mode: 'quick',
      dropped_conditions: ['部门 = 财务部'],
      mentions: [
        { type: 'document', id: 'kb1/d1', name: '差旅办法.pdf', base: 'builtin.kb1', base_name: '制度库' },
      ],
    });
    expect(cancel?.path).toBe('/api/workspaces/default/conversations/c1/cancel');
    expect(decide).toMatchObject({
      path: '/api/workspaces/default/conversations/c1/approvals/ap%201',
      body: { allow: true },
    });
    expect(rename).toMatchObject({ method: 'PATCH', body: { title: '新名' } });
    expect(remove?.method).toBe('DELETE');
    expect(eventsUrl('c 1')).toBe('/api/workspaces/default/conversations/c%201/events');
  });

  it('列表分页：带游标、解析下一页，坏数据丢弃', async () => {
    const api = routeFetch({
      'GET /api/workspaces/default/conversations': jsonResponse({
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

  it('新建会话带上第一条消息的 @ 与回答方式（没有 @ 时不带）', async () => {
    const api = routeFetch({
      'POST /api/workspaces/default/conversations': jsonResponse({ id: 'c1', title: 't' }, 201),
    });
    const mention = {
      type: 'document',
      id: 'kb1/d1',
      name: '合同模板',
      base: 'builtin.kb1',
      baseName: '合同库',
    } as const;
    await createConversation('合同模板有哪些内容', { answerMode: 'agent', mentions: [mention] });
    await createConversation('你好', { answerMode: 'agent' });
    expect(api.calls.map((c) => c.body)).toEqual([
      {
        title: '合同模板有哪些内容',
        first_turn: {
          answer_mode: 'agent',
          mentions: [
            { type: 'document', id: 'kb1/d1', name: '合同模板', base: 'builtin.kb1', base_name: '合同库' },
          ],
        },
      },
      { title: '你好' },
    ]);
  });

  it('创建会话返回不完整时报错', async () => {
    routeFetch({ 'POST /api/workspaces/default/conversations': jsonResponse({}, 201) });
    await expect(createConversation('x')).rejects.toThrow('服务返回的数据不完整');
    routeFetch({ 'GET /api/workspaces/default/conversations': jsonResponse('bad') });
    await expect(listConversations()).resolves.toEqual({ items: [], next: null });
  });
});
