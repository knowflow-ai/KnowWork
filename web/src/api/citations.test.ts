import { afterEach, describe, expect, it, vi } from 'vitest';
import { listCitations } from './citations';
import { jsonResponse, routeFetch } from '../test/render';

afterEach(() => vi.unstubAllGlobals());

describe('citations api', () => {
  it('按编号收成出处表，坏的条目丢掉', async () => {
    const api = routeFetch({
      'GET /api/conversations/*/citations': jsonResponse({
        items: [
          {
            number: 1,
            title: '合同.pdf',
            excerpt: '付款期限 30 天',
            page: 3,
            openUrl: 'http://kf/doc?page=3',
          },
          { number: 2, title: '无页码.md', excerpt: '', page: null, openUrl: null },
          { number: 'x', title: '编号不对' },
          { number: 3 },
        ],
        truncated: false,
      }),
    });
    const sources = await listCitations('a/b');
    expect(api.calls[0]?.path).toBe('/api/conversations/a%2Fb/citations');
    expect(sources).toEqual({
      1: { title: '合同.pdf', excerpt: '付款期限 30 天', page: 3, url: 'http://kf/doc?page=3' },
      2: { title: '无页码.md', excerpt: undefined, page: undefined, url: undefined },
    });
  });

  it('只接受 http(s) 的原文链接，别的协议当作没有', async () => {
    routeFetch({
      'GET /api/conversations/*/citations': jsonResponse({
        items: [{ number: 1, title: 't', openUrl: 'javascript:alert(1)' }],
      }),
    });
    expect((await listCitations('c1'))[1]?.url).toBeUndefined();
  });
});
