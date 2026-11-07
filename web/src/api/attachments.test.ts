import { afterEach, describe, expect, it, vi } from 'vitest';
import { attachmentStates, discardAttachment, stageAttachment } from './attachments';
import { jsonResponse, routeFetch } from '../test/render';

const BASE = '/api/workspaces/default/attachments';

afterEach(() => vi.unstubAllGlobals());

describe('附件接口', () => {
  it('暂存：一个文件一个请求，传到工作空间下（还没有会话也行），返回解析状态', async () => {
    const { calls, fetchMock } = routeFetch({
      [`POST ${BASE}`]: jsonResponse({
        items: [{ id: 'att_1', filename: '年报.pdf', size: 3, status: 'staged', parseStatus: 'parsing' }],
        failures: [],
      }),
    });
    vi.stubGlobal('fetch', fetchMock);
    const staged = await stageAttachment(new File(['%PDF'], '年报.pdf'));
    expect(staged).toEqual({
      id: 'att_1',
      filename: '年报.pdf',
      size: 3,
      status: 'staged',
      parse: 'parsing',
      digest: 'none',
    });
    expect(((calls[0]?.body as FormData).get('files') as File).name).toBe('年报.pdf');
  });

  it('没收下：抛出带文件名和原因的错误', async () => {
    const { fetchMock } = routeFetch({
      [`POST ${BASE}`]: jsonResponse({
        items: [],
        failures: [{ filename: 'a.exe', code: 'unsupported_format', message: '格式不支持' }],
      }),
    });
    vi.stubGlobal('fetch', fetchMock);
    await expect(stageAttachment(new File(['MZ'], 'a.exe'))).rejects.toThrow('a.exe：格式不支持');
  });

  it('查进度按 id 查，没有 id 不发请求；不解析的文件状态是 none', async () => {
    const { calls, fetchMock } = routeFetch({
      [`GET ${BASE}`]: jsonResponse({
        items: [
          {
            id: 'att_1',
            filename: 'a.pdf',
            size: 1,
            status: 'staged',
            parseStatus: 'ready',
            digestStatus: 'digesting',
            digestDone: 2,
            digestTotal: 7,
          },
          { id: 'att_2', filename: 'b.txt', size: 1, status: 'staged', parseStatus: null },
        ],
      }),
    });
    vi.stubGlobal('fetch', fetchMock);
    expect(await attachmentStates([])).toEqual([]);
    expect(calls).toHaveLength(0);
    const states = await attachmentStates(['att_1', 'att_2']);
    expect(states.map((s) => s.parse)).toEqual(['ready', 'none']);
    expect(states[0]?.digest).toBe('digesting');
    expect(states[0]?.digestProgress).toEqual([2, 7]);
    expect(calls[0]?.query.get('ids')).toBe('att_1,att_2');
  });

  it('移除：删掉服务端那份', async () => {
    const { calls, fetchMock } = routeFetch({
      [`DELETE ${BASE}/*`]: new Response(null, { status: 204 }),
    });
    vi.stubGlobal('fetch', fetchMock);
    await discardAttachment('att_1');
    expect(calls[0]?.path).toBe(`${BASE}/att_1`);
  });
});
