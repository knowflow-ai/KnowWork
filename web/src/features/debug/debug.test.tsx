import { afterEach, describe, expect, it, vi } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '../../test/user';
import { jsonResponse, renderWithApp, routeFetch } from '../../test/render';
import { ownRun } from '../../api/debug';
import RunInspector from './RunInspector';
import { messageLabel, prettyJson } from './format';

afterEach(() => vi.unstubAllGlobals());

const B = '/api/conversations/c1/debug';
const step = (n: number, extra = {}) => ({
  step: n,
  durationMs: 1200,
  reasoning: '',
  text: '',
  failures: [],
  usage: null,
  toolCalls: [
    {
      callId: `k${n}`,
      name: n === 1 ? 'bash' : 'write',
      arguments: '{"cmd":"ls"}',
      result: 'a.txt',
      isError: false,
      durationMs: 300,
    },
  ],
  ...extra,
});

function api() {
  return routeFetch({
    [`GET ${B}/turns/3`]: jsonResponse({
      summary: {
        turn: 3,
        status: 'failed',
        request: '导出 word',
        steps: 2,
        toolCalls: 2,
        errors: 1,
        inputTokens: 1500,
        outputTokens: 30,
        cost: null,
        durationMs: 4200,
        startedMs: 1,
      },
      steps: [step(1, { failures: ['模型调用失败：504 超时'] }), step(2, { text: '已生成' })],
      subagents: [],
      events: ['审批 write：批准本次'],
    }),
    [`GET ${B}/turns/3/steps/2/input`]: jsonResponse({
      system: ['你是 KnowWork 企业智能助手'],
      tools: [{ name: 'bash', description: '执行命令' }],
      notes: ['按会话日志还原'],
      messages: [
        { role: 'user', source: 'user', blocks: [{ type: 'text', text: '导出 word' }] },
        { role: 'user', source: 'skill-invocation', blocks: [{ type: 'text', text: '技能正文' }] },
      ],
    }),
  });
}

describe('RunInspector', () => {
  const steps = () => screen.getByRole('list', { name: '步骤' });

  it('顶部汇总与事件；左栏每一步一行，默认选中第一步，右栏是它的失败原因与工具调用；单色，导出指向原始日志', async () => {
    api();
    renderWithApp(<RunInspector source={ownRun('c1')} turn={3} onClose={() => undefined} />);
    expect(await screen.findByText(/失败 · 2 步 · 调用工具 2 次 .*失败 1 处/)).toBeInTheDocument();
    expect(screen.getByText('导出 word')).toBeInTheDocument();
    expect(screen.getByText('审批 write：批准本次')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /导出日志/ })).toHaveAttribute('href', `${B}/turns/3/export`);
    expect(within(steps()).getAllByRole('button')).toHaveLength(2);
    expect(within(steps()).getByText('有失败')).toBeInTheDocument();
    const first = screen.getByRole('article', { name: '第 1 步详情' });
    expect(within(first).getByText('模型调用失败：504 超时')).toBeInTheDocument();
    expect(within(first).getByText(/"cmd": "ls"/)).toBeInTheDocument();
    await userEvent.click(within(steps()).getByText('write'));
    const second = screen.getByRole('article', { name: '第 2 步详情' });
    expect(within(second).getByText('已生成')).toBeInTheDocument();
  });

  it('逐步回放：上一步只剩第一步，下一步再出现第二步并选中它', async () => {
    api();
    renderWithApp(<RunInspector source={ownRun('c1')} turn={3} onClose={() => undefined} />);
    await screen.findByText('第 2 / 2 步');
    await userEvent.click(screen.getByRole('button', { name: '上一步' }));
    expect(within(steps()).queryByText('第 2 步')).not.toBeInTheDocument();
    expect(screen.getByText('第 1 / 2 步')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '下一步' }));
    expect(within(steps()).getByText('第 2 步')).toBeInTheDocument();
    expect(screen.getByRole('article', { name: '第 2 步详情' })).toBeInTheDocument();
  });

  it('查看某一步的输入：系统提示、工具与消息，宿主塞进去的上下文标明来源', async () => {
    api();
    renderWithApp(<RunInspector source={ownRun('c1')} turn={3} onClose={() => undefined} />);
    await userEvent.click(await screen.findByText('write'));
    await userEvent.click(await screen.findByRole('button', { name: '查看这一步的输入' }));
    const dialog = await screen.findByRole('dialog', { name: '第 2 步的输入' });
    expect(await within(dialog).findByText('按会话日志还原')).toBeInTheDocument();
    expect(within(dialog).getByText('技能内容')).toBeInTheDocument();
    expect(within(dialog).getByText('可用工具（1 个）')).toBeInTheDocument();
  });

  it('文案：消息来源与 JSON 排版', () => {
    expect(messageLabel('user', 'user')).toBe('用户');
    expect(messageLabel('user', 'runtime-context')).toBe('运行上下文');
    expect(messageLabel('tool', null)).toBe('工具结果');
    expect(prettyJson('not json')).toBe('not json');
  });
});
