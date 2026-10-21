import { describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '../../../test/user';
import { renderWithApp } from '../../../test/render';
import { ApprovalCard } from './ApprovalCard';
import { AnswerActions } from './AnswerActions';
import { AssistantTurn } from './AssistantTurn';
import { Composer } from './Composer';
import { FileCard } from './FileCard';
import { Markdown } from './Markdown';
import { ProcessSummary } from './ProcessSummary';
import type { ApprovalView, RunView } from '../events/types';

const endedTurn: RunView = {
  runId: 'run-1',
  status: 'completed',
  startedAt: 0,
  endedAt: 38_000,
  notes: ['我先读取文件'],
  thinking: ['想一想'],
  answer: '答',
  problems: ['部分执行过程没有记录下来'],
  asked: [],
  trail: [
    { type: 'thinking', text: '想一想' },
    { type: 'note', text: '我先读取文件' },
    { type: 'step', callId: 'c1' },
    { type: 'step', callId: 'c2' },
  ],
  steps: [
    {
      callId: 'c1',
      kind: 'tool',
      name: 'read_file',
      arguments: '{"path":"a.txt"}',
      status: 'done',
      startedAt: 0,
      endedAt: 2000,
      resultPreview: '内容',
    },
    { callId: 'c2', kind: 'tool', name: 'web', arguments: '', status: 'error', errorReason: '网络不可用' },
  ],
};

describe('ProcessSummary', () => {
  it('默认收起成一行摘要，点开按先后列出说明与每一步，不展示思考原文', async () => {
    renderWithApp(<ProcessSummary run={endedTurn} />);
    const toggle = screen.getByRole('button', { name: /已完成 2 个步骤 · 用时 38 秒/ });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByText('read_file')).not.toBeInTheDocument();
    await userEvent.click(toggle);
    expect(screen.queryByText('想一想')).toBeNull();
    expect(screen.getByText('我先读取文件')).toBeInTheDocument();
    expect(screen.getByText('read_file')).toBeInTheDocument();
    expect(screen.getByText('path: a.txt')).toBeInTheDocument();
    expect(screen.getByText(/网络不可用/)).toBeInTheDocument();
    expect(screen.getByText('部分执行过程没有记录下来')).toBeInTheDocument();
    const texts = [...document.querySelectorAll('.kw-step-text')].map((el) => el.textContent);
    expect(texts).toEqual(['我先读取文件']);
  });

  it('步骤用人话标题，参数与结果默认收起，点开在代码框里看（JSON 格式化）', async () => {
    const run: RunView = {
      ...endedTurn,
      trail: [{ type: 'step', callId: 'k1' }],
      steps: [
        {
          callId: 'k1',
          kind: 'tool',
          name: 'knowledge_search',
          arguments: '{"query":"微短剧 代表作品"}',
          status: 'done',
          startedAt: 0,
          endedAt: 15_000,
          resultPreview: '{"results":[{"ref":1}]}',
        },
      ],
    };
    renderWithApp(<ProcessSummary run={run} />);
    await userEvent.click(screen.getByRole('button', { name: /已完成/ }));
    expect(screen.getByText('检索知识库')).toBeInTheDocument();
    expect(screen.getByText('微短剧 代表作品')).toBeInTheDocument();
    expect(screen.getByText('15 秒')).toBeInTheDocument();
    expect(screen.queryByText('结果')).toBeNull();
    await userEvent.click(screen.getByText('检索知识库'));
    expect(await screen.findByText('结果')).toBeInTheDocument();
    expect(document.querySelector('.kw-step-code pre')?.textContent).toContain('"query": "微短剧 代表作品"');
  });

  it('只有思考没有步骤时不可展开（思考原文不展示）', () => {
    renderWithApp(
      <ProcessSummary
        run={{
          ...endedTurn,
          steps: [],
          notes: [],
          problems: [],
          trail: [{ type: 'thinking', text: '想一想' }],
        }}
      />,
    );
    expect(screen.getByRole('button', { name: /已完成/ })).toBeDisabled();
    expect(screen.queryByText('想一想')).toBeNull();
  });

  it('运行中显示当前步骤', () => {
    renderWithApp(
      <ProcessSummary
        run={{
          ...endedTurn,
          endedAt: undefined,
          steps: [{ callId: 'c', kind: 'tool', name: 'bash', arguments: '', status: 'running' }],
        }}
      />,
    );
    expect(screen.getByText('正在运行命令')).toBeInTheDocument();
  });
});

describe('ApprovalCard', () => {
  const approval: ApprovalView = {
    id: 'ap1',
    toolName: 'write_file',
    status: 'pending',
    reason: '要写入报告文件',
  };

  it('说明工具与目标；勾「本会话内同类操作不再询问」再批准会带上记住，拒绝不带', async () => {
    const onDecide = vi.fn(async () => undefined);
    renderWithApp(
      <ApprovalCard
        approval={approval}
        step={{
          callId: 'c',
          kind: 'tool',
          name: 'write_file',
          arguments: '{"path":"报告.docx"}',
          status: 'awaiting-approval',
        }}
        onDecide={onDecide}
      />,
    );
    expect(screen.getByText('要写入报告文件')).toBeInTheDocument();
    expect(screen.getByText('工具：write_file · 目标：path: 报告.docx')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('checkbox', { name: '本会话内同类操作不再询问' }));
    await userEvent.click(screen.getByRole('button', { name: '批准' }));
    expect(onDecide).toHaveBeenCalledWith(true, true);
    await waitFor(() => expect(screen.getByRole('button', { name: '拒绝' })).toBeEnabled());
    await userEvent.click(screen.getByRole('button', { name: '拒绝' }));
    expect(onDecide).toHaveBeenLastCalledWith(false, false);
  });

  it('MCP 工具在标题与说明里都显示成「服务名 · 工具名」', () => {
    const name = 'mcp__deepwiki__read_wiki_contents';
    renderWithApp(
      <ApprovalCard
        approval={{ id: 'ap2', toolName: name, status: 'pending', reason: `写入操作「${name}」需要确认` }}
        onDecide={vi.fn()}
      />,
    );
    expect(screen.getByText('写入操作「deepwiki · read_wiki_contents」需要确认')).toBeInTheDocument();
    expect(screen.getByText('工具：deepwiki · read_wiki_contents')).toBeInTheDocument();
  });

  it('已处理的审批收成一行：结果 · 工具 · 目标，不再是卡片', () => {
    renderWithApp(
      <ApprovalCard
        approval={{ ...approval, toolName: 'mcp__deepwiki__read_wiki_structure', status: 'allowed' }}
        step={{
          callId: 'c',
          kind: 'tool',
          name: 'mcp__deepwiki__read_wiki_structure',
          arguments: '{"repoName":"Zleap-AI/SAG"}',
          status: 'done',
        }}
        onDecide={vi.fn()}
      />,
    );
    expect(
      screen.getByText('已批准 · deepwiki · read_wiki_structure · repoName: Zleap-AI/SAG'),
    ).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: '需要你批准的操作' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '批准' })).not.toBeInTheDocument();
  });
});

describe('Composer', () => {
  it('空内容不发送，回车发送后清空', async () => {
    const onSend = vi.fn(async () => undefined);
    renderWithApp(<Composer running={false} onSend={onSend} />);
    const input = screen.getByRole('textbox');
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(screen.getByText('请输入内容')).toBeInTheDocument());
    expect(onSend).not.toHaveBeenCalled();
    await userEvent.type(input, '你好');
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(onSend).toHaveBeenCalledWith('你好', 'followup', []));
    await waitFor(() => expect(input).toHaveValue(''));
    expect(screen.getByText('回答可能有误，重要结论请点开出处核对')).toBeInTheDocument();
  });

  it('运行中 Ctrl+回车 补充到当前任务，并能停止', async () => {
    const onSend = vi.fn(async () => undefined);
    const onStop = vi.fn(async () => undefined);
    renderWithApp(<Composer running onSend={onSend} onStop={onStop} />);
    const input = screen.getByRole('textbox');
    await userEvent.type(input, '再加一点');
    fireEvent.keyDown(input, { key: 'Enter', ctrlKey: true });
    await waitFor(() => expect(onSend).toHaveBeenCalledWith('再加一点', 'steer', []));
    expect(screen.getByText(/Ctrl\/⌘ \+ 回车：补充到当前任务/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '停止' }));
    expect(onStop).toHaveBeenCalled();
  });

  it('附件：选文件后显示在输入框上方，可以移除，随消息发出后清空；超过 20 MiB 的不加入', async () => {
    const onSend = vi.fn(async () => undefined);
    renderWithApp(<Composer running={false} onSend={onSend} />);
    const contract = new File(['合同正文'], '合同.docx');
    const sheet = new File(['1,2'], '明细.csv');
    const huge = new File(['x'], '录像.mp4');
    Object.defineProperty(huge, 'size', { value: 21 * 1024 * 1024 });
    fireEvent.change(screen.getByLabelText('选择文件'), { target: { files: [contract, sheet, huge] } });
    expect(await screen.findByText('录像.mp4 超过 20 MiB，没有加入')).toBeInTheDocument();
    const rail = screen.getByRole('list', { name: '待发送的附件' });
    expect(within(rail).getByText('合同.docx')).toBeInTheDocument();
    await userEvent.click(within(rail).getByRole('button', { name: '移除 明细.csv' }));
    expect(within(rail).queryByText('明细.csv')).not.toBeInTheDocument();
    await userEvent.type(screen.getByRole('textbox'), '改一下付款条款');
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' });
    await waitFor(() => expect(onSend).toHaveBeenCalledWith('改一下付款条款', 'followup', [contract]));
    await waitFor(() => expect(screen.queryByRole('list', { name: '待发送的附件' })).not.toBeInTheDocument());
  });

  it('发送失败时保留内容并提示', async () => {
    const onSend = vi.fn(async () => Promise.reject(new Error('上一条消息还在处理，请稍后再试')));
    renderWithApp(<Composer running={false} onSend={onSend} />);
    const input = screen.getByRole('textbox');
    await userEvent.type(input, '你好');
    await userEvent.click(screen.getByRole('button', { name: '发送' }));
    await waitFor(() => expect(screen.getByText('上一条消息还在处理，请稍后再试')).toBeInTheDocument());
    expect(input).toHaveValue('你好');
  });

  it('选择技能会插入技能名', async () => {
    renderWithApp(<Composer running={false} onSend={vi.fn()} skills={[{ key: 's1', name: '周报生成' }]} />);
    await userEvent.click(screen.getByRole('button', { name: '选择技能' }));
    await userEvent.click(await screen.findByRole('menuitem', { name: '周报生成' }));
    expect(screen.getByRole('textbox')).toHaveValue('/周报生成 ');
  });
});

describe('Markdown 与引用', () => {
  it('引用渲染成上标，点开看出处', async () => {
    renderWithApp(
      <Markdown
        text="结论成立[ID:1]，参见[文档](https://example.com)"
        sources={{ 1: { title: '员工手册', excerpt: '第三条' } }}
      />,
    );
    const cite = screen.getByRole('button', { name: '查看出处 1' });
    await userEvent.click(cite);
    expect(await screen.findByText('员工手册')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '文档' })).toHaveAttribute('target', '_blank');
  });

  it('没有出处数据时说明暂不可用，不渲染原始 HTML', async () => {
    const { container } = renderWithApp(<Markdown text={'<script>x</script>\n\n见[^2]'} />);
    expect(container.querySelector('script')).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: '查看出处 2' }));
    expect(await screen.findByText('出处原文暂不可用')).toBeInTheDocument();
  });
});

describe('引用出处与无效编号', () => {
  it('[n] 渲染成上标，点开显示标题、页码、摘录与查看原文', async () => {
    renderWithApp(
      <Markdown
        text="付款期限 30 天[1][2]。"
        sources={{
          1: {
            title: '采购合同.pdf',
            page: 3,
            excerpt: '乙方应在 30 天内付款',
            url: 'https://kf/doc?page=3',
          },
          2: { title: '补充协议.docx' },
        }}
      />,
    );
    await userEvent.click(screen.getByRole('button', { name: '查看出处 1' }));
    expect(await screen.findByText('采购合同.pdf')).toBeInTheDocument();
    expect(screen.getByText('第 3 页')).toBeInTheDocument();
    expect(screen.getByText('乙方应在 30 天内付款')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '查看原文' })).toHaveAttribute('href', 'https://kf/doc?page=3');
    expect(screen.getByRole('button', { name: '查看出处 2' })).toBeInTheDocument();
  });

  it('引用目录拉到后，目录里没有的编号不显示，回答下方说明去掉了哪些', () => {
    const run: RunView = { ...endedTurn, answer: '期限 30 天[1]，违约金 5%[7]。' };
    renderWithApp(
      <AssistantTurn
        run={run}
        approvals={[]}
        questions={[]}
        artifacts={[]}
        sources={{ 1: { title: '采购合同.pdf' } }}
        onDecide={vi.fn()}
        onAnswer={vi.fn(async () => true)}
        busy={false}
      />,
    );
    expect(screen.getByRole('button', { name: '查看出处 1' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '查看出处 7' })).toBeNull();
    expect(screen.getByText('已去掉 1 处没有出处的引用编号：[7]')).toBeInTheDocument();
  });

  it('引用目录还没拉到时编号照常显示，不提示', () => {
    const run: RunView = { ...endedTurn, answer: '见[7]。' };
    renderWithApp(
      <AssistantTurn
        run={run}
        approvals={[]}
        questions={[]}
        artifacts={[]}
        onDecide={vi.fn()}
        onAnswer={vi.fn(async () => true)}
        busy={false}
      />,
    );
    expect(screen.getByRole('button', { name: '查看出处 7' })).toBeInTheDocument();
    expect(screen.queryByText(/已去掉/)).toBeNull();
  });
});

describe('FileCard 与 AnswerActions', () => {
  it('文件卡片显示类型与大小', () => {
    renderWithApp(<FileCard name="周报.docx" size={2048} url="/f/1" />);
    expect(screen.getByText('Word 文档 · 2.0 KB')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '下载 周报.docx' })).toHaveAttribute('href', '/f/1');
  });

  it('没有地址时下载不可用', () => {
    renderWithApp(<FileCard name="x.bin" />);
    expect(screen.getByRole('button', { name: '下载 x.bin' })).toBeDisabled();
  });

  it('复制、反馈与重新生成', async () => {
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    const onRegenerate = vi.fn();
    renderWithApp(<AnswerActions text="答案" onRegenerate={onRegenerate} />);
    await userEvent.click(screen.getByRole('button', { name: '复制' }));
    expect(writeText).toHaveBeenCalledWith('答案');
    const up = screen.getByRole('button', { name: '有帮助' });
    await userEvent.click(up);
    expect(up).toHaveAttribute('aria-pressed', 'true');
    await userEvent.click(screen.getByRole('button', { name: '没帮助' }));
    expect(up).toHaveAttribute('aria-pressed', 'false');
    await userEvent.click(screen.getByRole('button', { name: '重新生成' }));
    expect(onRegenerate).toHaveBeenCalled();
  });

  it('复制失败时提示', async () => {
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: vi.fn(async () => Promise.reject(new Error('denied'))) },
      configurable: true,
    });
    renderWithApp(<AnswerActions text="答案" />);
    await userEvent.click(screen.getByRole('button', { name: '复制' }));
    expect(await screen.findByText('复制没有成功，请手动选中文字复制')).toBeInTheDocument();
  });

  it('运行详情入口：有正文时排在最后；这一轮没有正文（例如失败了）时只留它', async () => {
    const onInspect = vi.fn();
    const { unmount } = renderWithApp(<AnswerActions text="答案" onInspect={onInspect} />);
    await userEvent.click(screen.getByRole('button', { name: '运行详情' }));
    expect(onInspect).toHaveBeenCalledTimes(1);
    unmount();
    renderWithApp(<AnswerActions text="" onRegenerate={() => undefined} onInspect={onInspect} />);
    expect(screen.getByRole('button', { name: '运行详情' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '复制' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '重新生成' })).not.toBeInTheDocument();
  });
});
