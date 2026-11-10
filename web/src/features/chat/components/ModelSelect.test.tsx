import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import { pickOption } from '../../../test/user';
import { jsonResponse, renderWithApp } from '../../../test/render';
import { listChatModels, sendMessage } from '../../../api/conversations';
import { NEW_CHAT, currentChatModel, useChatModel } from '../../../stores/chatModel';
import { ModelSelect } from './ModelSelect';

let models: unknown = { items: [], selected: null };
const asked: string[] = [];

beforeEach(() => {
  asked.length = 0;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      asked.push(`${init?.method ?? 'GET'} ${url} ${typeof init?.body === 'string' ? init.body : ''}`);
      if (url.startsWith('/api/workspaces/default/chat-models')) return jsonResponse(models);
      if (url.endsWith('/messages')) return jsonResponse({ messageId: 'm1' });
      return jsonResponse({}, 404);
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  useChatModel.setState({ chosen: {}, defaults: {} });
});

const fast = { alias: 'fast', name: 'deepseek-chat', vision: false, default: true };
const eye = { alias: 'eye', name: 'qwen-vl', vision: true, default: false };

describe('对话模型下拉', () => {
  it('默认选服务端给的（上次用的），选了别的按对话记，发消息带上', async () => {
    models = { items: [fast, eye], selected: 'fast' };
    renderWithApp(<ModelSelect conversationId="c1" />);
    await waitFor(() => expect(currentChatModel('c1')).toBe('fast'));
    expect(asked[0]).toContain('/chat-models?conversation_id=c1');
    await pickOption(document.body, '对话模型', /eye/);
    expect(currentChatModel('c1')).toBe('eye');
    expect(currentChatModel(NEW_CHAT)).toBeUndefined();
    expect(screen.getByRole('group', { name: '对话模型' })).toHaveTextContent('eye');
    await sendMessage('c1', '你好', 'followup', { model: currentChatModel('c1') });
    expect(asked.at(-1)).toContain('"model":"eye"');
  });

  it('只有一个模型时只显示、不能换；没有对话模型时不显示', async () => {
    models = { items: [fast], selected: 'fast' };
    const { unmount } = renderWithApp(<ModelSelect />);
    await waitFor(() => expect(screen.getByRole('group', { name: '对话模型' })).toHaveTextContent('fast'));
    // 组件库的触发器是 role=button 的 div，禁用时标 aria-disabled
    expect(within(screen.getByRole('group', { name: '对话模型' })).getByRole('button')).toHaveAttribute(
      'aria-disabled',
      'true',
    );
    unmount();
    models = { items: [], selected: null };
    renderWithApp(<ModelSelect conversationId="c2" />);
    await waitFor(() => expect(asked.some((a) => a.includes('conversation_id=c2'))).toBe(true));
    expect(screen.queryByRole('group', { name: '对话模型' })).toBeNull();
  });

  it('新对话页的选择建好对话后带过去，新对话页下次从头按服务端的默认', () => {
    const { choose, setDefault, carry } = useChatModel.getState();
    setDefault(NEW_CHAT, 'fast');
    choose(NEW_CHAT, 'eye');
    carry(NEW_CHAT, 'c9');
    expect(currentChatModel('c9')).toBe('eye');
    expect(currentChatModel(NEW_CHAT)).toBe('fast');
    setDefault(NEW_CHAT, null);
    expect(currentChatModel(NEW_CHAT)).toBeUndefined();
  });

  it('解析服务端的清单：默认选的不在清单里就当没有', async () => {
    models = { items: [fast, { alias: '' }, eye], selected: 'gone' };
    await expect(listChatModels()).resolves.toEqual({
      items: [
        { alias: 'fast', name: 'deepseek-chat', vision: false, isDefault: true },
        { alias: 'eye', name: 'qwen-vl', vision: true, isDefault: false },
      ],
      selected: null,
    });
  });
});
