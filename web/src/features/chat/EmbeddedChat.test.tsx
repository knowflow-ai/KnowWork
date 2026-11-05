import { useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import userEvent from '../../test/user';
import { renderWithApp, jsonResponse } from '../../test/render';
import type { StreamHandlers } from '../../api/sse';
import { ev } from './events/fixtures';
import type { WireEvent } from './events/types';

const streams: StreamHandlers[] = [];
vi.mock('../../api/sse', () => ({
  openEventStream: (_options: unknown, handlers: StreamHandlers) => {
    streams.push(handlers);
    return () => undefined;
  },
}));

const { EmbeddedChat } = await import('./EmbeddedChat');

let calls: { url: string; method?: string; body?: string }[] = [];

const push = (event: WireEvent) => act(() => streams[streams.length - 1]?.onEvent(event));

beforeEach(() => {
  streams.length = 0;
  calls = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, method: init?.method, body: init?.body as string | undefined });
      if (url === '/api/workspaces/default/conversations' && init?.method === 'POST')
        return jsonResponse({ id: 'c-new', title: 't' }, 201);
      if (url.endsWith('/mention-options')) return jsonResponse({ bases: [], services: [], skills: [] });
      return jsonResponse({ messageId: 'm1' }, 202);
    }),
  );
});

afterEach(() => vi.unstubAllGlobals());

const own = (url: string) => !/\/(artifacts|mention-options|feedback|answer-modes)$/.test(url);

describe('EmbeddedChat', () => {
  it('第一句：建会话、先让外层绑定、再按固定的回答方式发出去；不显示回答方式下拉', async () => {
    const order: string[] = [];
    const onCreated = vi.fn(async (id: string) => {
      order.push(`bind ${id}`);
    });
    renderWithApp(<EmbeddedChat answerMode="ext_mode" hint="说说要改什么" onCreated={onCreated} />);
    expect(screen.getByText('说说要改什么')).toBeInTheDocument();
    expect(screen.queryByLabelText('回答方式')).not.toBeInTheDocument();
    await userEvent.type(screen.getByRole('textbox'), '给这张表起中文名');
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' });
    await waitFor(() => expect(screen.getByText('给这张表起中文名')).toBeInTheDocument());
    expect(onCreated).toHaveBeenCalledWith('c-new');
    const sent = calls.filter((c) => own(c.url));
    expect(sent.map((c) => c.url)).toEqual([
      '/api/workspaces/default/conversations',
      '/api/workspaces/default/conversations/c-new/messages',
    ]);
    expect(JSON.parse(sent[1]?.body ?? '{}')).toMatchObject({ answer_mode: 'ext_mode' });
    expect(screen.queryByLabelText('回答方式')).not.toBeInTheDocument();
  });

  it('还没开始时摆着标题、说明、现成问题与外层给的一块；点现成问题就照这句建会话发出去', async () => {
    const onCreated = vi.fn(async () => undefined);
    renderWithApp(
      <EmbeddedChat
        answerMode="ext_mode"
        heading="让 AI 帮你整理"
        hint="起中文名、写说明"
        starters={['给订单表起中文名']}
        extra={<button type="button">后台起草</button>}
        footnote="改动要你批准才生效"
        onCreated={onCreated}
      />,
    );
    expect(screen.getByText('让 AI 帮你整理')).toBeInTheDocument();
    expect(screen.getByText('改动要你批准才生效')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '后台起草' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '给订单表起中文名' }));
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith('c-new'));
    const message = calls.find((c) => c.url.endsWith('/c-new/messages'))?.body ?? '{}';
    expect(JSON.parse(message)).toMatchObject({ answer_mode: 'ext_mode' });
    expect(JSON.stringify(JSON.parse(message))).toContain('给订单表起中文名');
  });

  it('已有会话：直接接着聊，后续消息也按固定的回答方式发', async () => {
    renderWithApp(<EmbeddedChat answerMode="ext_mode" conversationId="c1" />);
    await userEvent.type(screen.getByRole('textbox'), '再补几个别名');
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' });
    await waitFor(() => expect(calls.some((c) => c.url.endsWith('/c1/messages'))).toBe(true));
    const body = calls.find((c) => c.url.endsWith('/c1/messages'))?.body ?? '{}';
    expect(JSON.parse(body)).toMatchObject({ answer_mode: 'ext_mode' });
  });

  it('外层绑定失败：不发首条，报出原因', async () => {
    const onCreated = vi.fn(async () => {
      throw new Error('没有这个语义项目');
    });
    renderWithApp(<EmbeddedChat answerMode="ext_mode" onCreated={onCreated} />);
    await userEvent.type(screen.getByRole('textbox'), '开始');
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' });
    await waitFor(() => expect(onCreated).toHaveBeenCalled());
    expect(calls.some((c) => c.url.endsWith('/messages'))).toBe(false);
  });

  it('一轮跑完告诉外层，页面据此刷新自己的数据', async () => {
    const onTurnEnd = vi.fn();
    renderWithApp(<EmbeddedChat answerMode="ext_mode" conversationId="c1" onTurnEnd={onTurnEnd} />);
    act(() => streams[0]?.onStatus?.('connected'));
    push(ev.runStarted(1));
    push(ev.message(2, '已发布第 2 版'));
    expect(onTurnEnd).not.toHaveBeenCalled();
    push(ev.runCompleted(3));
    await waitFor(() => expect(onTurnEnd).toHaveBeenCalledTimes(1));
  });

  it('页面上正在看的对象：输入框上方写明，下一条开头带上，只带一次；点掉就不带', async () => {
    const amount = {
      key: 'fd:orders/amount',
      label: '正在看：订单 · 下单金额',
      text: '（说的是字段 orders.amount）',
    };
    const gmv = { key: 'mt:gmv', label: '正在看：成交额', text: '（说的是指标 gmv）' };
    function Page() {
      const [context, setContext] = useState(amount);
      return (
        <>
          <button type="button" onClick={() => setContext(gmv)}>
            换成成交额
          </button>
          <EmbeddedChat answerMode="ext_mode" conversationId="c1" context={context} />
        </>
      );
    }
    renderWithApp(<Page />);
    expect(screen.getByText('正在看：订单 · 下单金额')).toBeInTheDocument();
    await userEvent.type(screen.getByRole('textbox'), '起个中文名');
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' });
    await waitFor(() => expect(calls.filter((c) => c.url.endsWith('/c1/messages'))).toHaveLength(1));
    expect(JSON.parse(calls.find((c) => c.url.endsWith('/c1/messages'))?.body ?? '{}').text).toBe(
      '（说的是字段 orders.amount）\n起个中文名',
    );
    expect(screen.queryByText('正在看：订单 · 下单金额')).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '换成成交额' }));
    expect(screen.getByText('正在看：成交额')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '不带上' }));
    expect(screen.queryByText('正在看：成交额')).not.toBeInTheDocument();
  });
});
