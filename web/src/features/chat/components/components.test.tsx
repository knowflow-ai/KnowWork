import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
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
    await waitFor(() => expect(onSend).toHaveBeenCalledWith('你好', 'followup', [], []));
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
    await waitFor(() => expect(onSend).toHaveBeenCalledWith('再加一点', 'steer', [], []));
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
    await waitFor(() => expect(onSend).toHaveBeenCalledWith('改一下付款条款', 'followup', [contract], []));
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
});

describe('逐字输出', () => {
  it('正在输出的文字接在已有回答后面显示，没结束时不出操作按钮', () => {
    const running: RunView = {
      ...endedTurn,
      status: 'running',
      endedAt: undefined,
      endReason: undefined,
      answer: '先说结论。',
    };
    renderWithApp(
      <AssistantTurn
        run={running}
        approvals={[]}
        questions={[]}
        artifacts={[]}
        onDecide={vi.fn()}
        onAnswer={vi.fn()}
        busy
        liveText="短剧规模继续增长"
      />,
    );
    expect(screen.getByText('先说结论。')).toBeInTheDocument();
    expect(screen.getByText('短剧规模继续增长')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '复制' })).toBeNull();
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

  it('回答里的图按出处换成图片，放在它说明的那句话后面；对不上或出处还没拉到时不显示', () => {
    const text = '报销分三步[1]。\n\n[IMG:1:0]\n\n[IMG:1:5]\n\n[IMG:2:0]';
    const { container, rerender } = renderWithApp(
      <Markdown
        text={text}
        sources={{
          1: { title: '差旅办法', images: [{ url: '/api/x/img1?sig=s', caption: '报销流程图' }] },
        }}
      />,
    );
    const image = screen.getByRole('img', { name: '报销流程图' });
    expect(image).toHaveAttribute('src', '/api/x/img1?sig=s');
    expect(image.closest('a')).toBeNull();
    expect(container.querySelectorAll('img')).toHaveLength(1);
    expect(container.textContent).not.toContain('IMG');
    rerender(<Markdown text={text} />);
    expect(container.querySelectorAll('img')).toHaveLength(0);
  });

  it('点图在看图层里放大，带图注；Esc 关掉，不跳新标签页', async () => {
    renderWithApp(
      <Markdown
        text="见下图[1]。\n\n[IMG:1:0]"
        sources={{ 1: { title: '差旅办法', images: [{ url: '/api/x/img1?sig=s', caption: '报销流程图' }] } }}
      />,
    );
    await userEvent.click(screen.getByRole('button', { name: '放大查看：报销流程图' }));
    const viewer = await screen.findByRole('dialog', { name: '报销流程图' });
    expect(within(viewer).getByRole('img', { name: '报销流程图' })).toHaveAttribute(
      'src',
      '/api/x/img1?sig=s',
    );
    expect(within(viewer).getByText('报销流程图', { selector: 'figcaption' })).toBeInTheDocument();
    await userEvent.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('回答过程中重新渲染、重新拉出处，图不重建（不重新加载、不闪）', () => {
    const text = '报销分三步[1]。\n\n[IMG:1:0]';
    const sources = () => ({
      1: { title: '差旅办法', images: [{ url: '/api/x/img1?sig=s', caption: '报销流程图' }] },
    });
    // 不用 renderWithApp：它的 rerender 会连外层 Provider 一起换掉，整棵树都会重挂
    const { rerender } = render(<Markdown text={text} sources={sources()} />);
    const image = screen.getByRole('img', { name: '报销流程图' });
    rerender(<Markdown text={`${text}\n\n审批后报销。`} sources={sources()} />);
    expect(screen.getByRole('img', { name: '报销流程图' })).toBe(image);
  });

  it('没有出处数据时说明暂不可用，不渲染原始 HTML', async () => {
    const { container } = renderWithApp(<Markdown text={'<script>x</script>\n\n见[^2]'} />);
    expect(container.querySelector('script')).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: '查看出处 2' }));
    expect(await screen.findByText('出处原文暂不可用')).toBeInTheDocument();
  });
});

describe('引用出处与无效编号', () => {
  it('回答里原样摘了原文才收窄到那一处（摘录、页码跟着换）；改写过的照样是整块', async () => {
    const source = {
      title: 'F-CM324C',
      page: 11,
      excerpt: '说明 若想设置左右摆头以本体为中心时……',
      spots: [
        { locate: { type: 'pdf' as const, page: 11, boxes: [], quotes: ['说明'] }, quote: '说明' },
        {
          locate: { type: 'pdf' as const, page: 12, boxes: [], quotes: ['注意 可动部位不可把手指伸入。'] },
          quote: '注意 可动部位不可把手指伸入。',
        },
      ],
    };
    renderWithApp(
      <Markdown
        text="手动调节时，可动部位不要把手指伸入[3]。说明书写着「注意：可动部位不可把手指伸入」[4]。"
        sources={{ 3: source, 4: source }}
      />,
    );
    await userEvent.click(screen.getByRole('button', { name: '查看出处 3' }));
    expect(await screen.findByText('说明 若想设置左右摆头以本体为中心时……')).toBeInTheDocument();
    expect(screen.getByText('第 11 页')).toBeInTheDocument();
    await userEvent.keyboard('{Escape}');
    await userEvent.click(screen.getByRole('button', { name: '查看出处 4' }));
    expect(await screen.findByText('注意 可动部位不可把手指伸入。')).toBeInTheDocument();
    expect(screen.getByText('第 12 页')).toBeInTheDocument();
  });

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

  it('回答结束后图标行末尾有来源按钮，答到一半时没有', () => {
    const sources = {
      1: { title: '采购合同.pdf', url: 'http://kf/1' },
      2: { title: '采购合同.pdf', url: 'http://kf/1' },
    };
    const props = {
      approvals: [],
      questions: [],
      artifacts: [],
      sources,
      onDecide: vi.fn(),
      onAnswer: vi.fn(async () => true),
      busy: false,
    };
    const { unmount } = renderWithApp(
      <AssistantTurn {...props} run={{ ...endedTurn, endedAt: undefined, answer: '期限 30 天[1]' }} />,
    );
    expect(screen.queryByRole('button', { name: /个来源/ })).toBeNull();
    unmount();
    renderWithApp(<AssistantTurn {...props} run={{ ...endedTurn, answer: '期限 30 天[1]，违约金[2]。' }} />);
    expect(screen.getByRole('button', { name: '1 个来源' })).toBeInTheDocument();
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

  it('复制回答时去掉图片编号（复制出去换不成图）', async () => {
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    renderWithApp(<AnswerActions text={'报销分三步[1]。\n\n[IMG:1:0]\n\n先填单。'} onRegenerate={vi.fn()} />);
    await userEvent.click(screen.getByRole('button', { name: '复制' }));
    expect(writeText).toHaveBeenCalledWith('报销分三步[1]。\n\n先填单。');
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
