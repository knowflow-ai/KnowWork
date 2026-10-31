import { afterEach, describe, expect, it, vi } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '../../test/user';
import { jsonResponse, renderWithApp, routeFetch } from '../../test/render';
import { ownRun } from '../../api/debug';
import RunInspector from './RunInspector';
import { messageLabel, prettyJson, statusLabel, statusTone } from './format';

afterEach(() => vi.unstubAllGlobals());

const B = '/api/workspaces/default/conversations/c1/debug';
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
      steps: [
        step(1, { failures: ['模型调用失败：504 超时'] }),
        step(2, {
          text: '已生成',
          toolCalls: [
            {
              callId: 'k2',
              name: 'knowledge_search',
              arguments: '{"query":"流程图"}',
              result: '{"documents":[]}',
              isError: false,
              durationMs: 300,
              trace: [
                {
                  label: '视觉检索',
                  summary: '查了 1 个知识库，命中 1 页',
                  lines: ['手册 第 3 页，分数 12.41，展开 2 块，进了结果第 1 名'],
                  source: '扩展知识库',
                },
              ],
            },
          ],
        }),
      ],
      subagents: [],
      events: ['审批 write：批准本次'],
      background: [
        { kind: 'standing', text: '<user_memory>[1] 回答用表格</user_memory>', sentTurn: 1 },
        {
          kind: 'turn',
          text: '<user_memory_recall>[2] 生产库是 PostgreSQL</user_memory_recall>',
          sentTurn: 3,
        },
      ],
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
    await userEvent.click(within(steps()).getByText('knowledge_search'));
    const second = screen.getByRole('article', { name: '第 2 步详情' });
    expect(within(second).getByText('已生成')).toBeInTheDocument();
    // 知识检索的过程按调用挂上，写明没有交给模型
    expect(within(second).getByText('检索过程（没有交给模型）')).toBeInTheDocument();
    expect(
      within(second).getByText(/视觉检索 · 扩展知识库：查了 1 个知识库，命中 1 页\s+手册 第 3 页/),
    ).toBeInTheDocument();
    expect(within(first).queryByText('检索过程（没有交给模型）')).not.toBeInTheDocument();
  });

  it('随提问带上的背景默认收起，点开是每轮都带的（写明沿用第几轮发的）与只给这一轮的', async () => {
    api();
    renderWithApp(<RunInspector source={ownRun('c1')} turn={3} onClose={() => undefined} />);
    const toggle = await screen.findByRole('button', { name: '随提问带上的背景' });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByText(/回答用表格/)).not.toBeInTheDocument();
    await userEvent.click(toggle);
    expect(screen.getByText('每轮都带 · 沿用第 1 轮发的')).toBeInTheDocument();
    expect(screen.getByText(/\[1\] 回答用表格/)).toBeInTheDocument();
    expect(screen.getByText('只给这一轮')).toBeInTheDocument();
    expect(screen.getByText(/生产库是 PostgreSQL/)).toBeInTheDocument();
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
    await userEvent.click(await screen.findByText('knowledge_search'));
    await userEvent.click(await screen.findByRole('button', { name: '查看这一步的输入' }));
    const dialog = await screen.findByRole('dialog', { name: '第 2 步的输入' });
    expect(await within(dialog).findByText('按会话日志还原')).toBeInTheDocument();
    expect(within(dialog).getByText('技能内容')).toBeInTheDocument();
    expect(within(dialog).getByText('可用工具（1 个）')).toBeInTheDocument();
  });

  it('文案：消息来源与 JSON 排版', () => {
    expect(messageLabel('user', 'user')).toBe('用户');
    expect(messageLabel('user', 'runtime-context')).toBe('运行上下文');
    expect(messageLabel('user', 'knowwork-turn-context')).toBe('随提问带上的背景');
    expect(messageLabel('tool', null)).toBe('工具结果');
    expect(prettyJson('not json')).toBe('not json');
  });
});

describe('运行状态的文案与颜色', () => {
  it('DSH 的 aborted（用户中止）和 cancelled 一样显示成已取消、灰色，不露出英文', () => {
    expect([statusLabel('aborted'), statusTone('aborted')]).toEqual(['已取消', 'default']);
    expect([statusLabel('cancelled'), statusTone('cancelled')]).toEqual(['已取消', 'default']);
    expect(statusTone('failed')).toBe('error');
  });
});
