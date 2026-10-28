import { afterEach, describe, expect, it, vi } from 'vitest';
import { listCitations } from './citations';
import { jsonResponse, routeFetch } from '../test/render';

afterEach(() => vi.unstubAllGlobals());

describe('citations api', () => {
  it('按编号收成出处表，坏的条目丢掉', async () => {
    const api = routeFetch({
      'GET /api/workspaces/default/conversations/*/citations': jsonResponse({
        items: [
          {
            number: 1,
            title: '合同.pdf',
            excerpt: '付款期限 30 天',
            page: 3,
            openUrl: 'http://kf/doc?page=3',
          },
          { number: 2, title: '无页码.md', excerpt: '', page: null, openUrl: null, versionNote: null },
          { number: 4, title: '旧版.md', excerpt: '', versionNote: '已被新版《新版》替代' },
          { number: 'x', title: '编号不对' },
          { number: 3 },
        ],
        truncated: false,
      }),
    });
    const sources = await listCitations('a/b');
    expect(api.calls[0]?.path).toBe('/api/workspaces/default/conversations/a%2Fb/citations');
    expect(sources).toEqual({
      1: { title: '合同.pdf', excerpt: '付款期限 30 天', page: 3, url: 'http://kf/doc?page=3' },
      2: { title: '无页码.md', excerpt: undefined, page: undefined, url: undefined },
      4: {
        title: '旧版.md',
        excerpt: undefined,
        page: undefined,
        url: undefined,
        versionNote: '已被新版《新版》替代',
      },
    });
  });

  it('一条引用就是一块：定位用这一块的全部位置合起来，点开整块高亮', async () => {
    routeFetch({
      'GET /api/workspaces/default/conversations/*/citations': jsonResponse({
        items: [
          {
            number: 1,
            title: '产品说明',
            page: 21,
            locator: { type: 'slide', page: 21, quote: '三步部署开启企业知识飞轮' },
            locators: [
              { type: 'slide', page: 21, quote: '三步部署开启企业知识飞轮' },
              { type: 'slide', page: 21, quote: '快速部署' },
            ],
          },
        ],
      }),
    });
    const [source] = Object.values(await listCitations('c1'));
    expect(source?.locate).toEqual({
      type: 'pdf',
      page: 21,
      boxes: [],
      quotes: ['三步部署开启企业知识飞轮', '快速部署'],
    });
    expect(source?.spots).toHaveLength(2);
  });

  it('图片只认站内地址与 http(s)，别的协议丢掉', async () => {
    routeFetch({
      'GET /api/workspaces/default/conversations/*/citations': jsonResponse({
        items: [
          {
            number: 1,
            title: 't',
            images: [
              { url: '/api/x/img1?sig=s', caption: '流程图' },
              { url: 'javascript:alert(1)', caption: '坏的' },
              { url: 'data:image/png;base64,xx' },
              { url: 'https://cdn.example.com/a.png' },
            ],
          },
        ],
      }),
    });
    expect((await listCitations('c1'))[1]?.images).toEqual([
      { url: '/api/x/img1?sig=s', caption: '流程图' },
      { url: 'https://cdn.example.com/a.png', caption: '' },
    ]);
  });

  it('只接受 http(s) 的原文链接，别的协议当作没有', async () => {
    routeFetch({
      'GET /api/workspaces/default/conversations/*/citations': jsonResponse({
        items: [{ number: 1, title: 't', openUrl: 'javascript:alert(1)' }],
      }),
    });
    expect((await listCitations('c1'))[1]?.url).toBeUndefined();
  });
});
