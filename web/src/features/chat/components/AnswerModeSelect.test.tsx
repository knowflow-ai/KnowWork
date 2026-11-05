import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { pickOption } from '../../../test/user';
import { jsonResponse, renderWithApp } from '../../../test/render';
import { BUILT_IN_ANSWER_MODES } from '../../../api/conversations';
import { currentAnswerMode, useAnswerMode } from '../../../stores/answerMode';
import { AnswerModeSelect } from './AnswerModeSelect';

let modes: unknown = { items: [] };

beforeEach(() => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) =>
      url === '/api/workspaces/default/answer-modes' ? jsonResponse(modes) : jsonResponse({}, 404),
    ),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  window.localStorage.clear();
  useAnswerMode.setState({ mode: 'agent', options: BUILT_IN_ANSWER_MODES });
});

describe('回答方式下拉', () => {
  it('列出服务端给的方式（含扩展登记的），选中后发消息带它', async () => {
    modes = {
      items: [
        { id: 'agent', label: '智能体', description: '能用技能' },
        { id: 'quick', label: '快速问答', description: '只查知识库' },
        { id: 'ext_mode', label: '扩展方式', description: '扩展登记的一种' },
      ],
    };
    renderWithApp(<AnswerModeSelect />);
    await waitFor(() => expect(useAnswerMode.getState().options).toHaveLength(3));
    await pickOption(document.body, '回答方式', /扩展方式/);
    expect(currentAnswerMode()).toBe('ext_mode');
    expect(screen.getByRole('group', { name: '回答方式' })).toHaveTextContent('扩展方式');
  });

  it('服务端没给可用的方式时用内置两种；记住的方式不在里面就按智能体', async () => {
    modes = { items: [] };
    useAnswerMode.setState({ mode: 'ext_mode' });
    renderWithApp(<AnswerModeSelect />);
    await waitFor(() => expect(screen.getByRole('group', { name: '回答方式' })).toHaveTextContent('智能体'));
    expect(currentAnswerMode()).toBe('agent');
  });
});
