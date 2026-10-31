/** 回答里的 mermaid 代码块画成图：写错了补引号再试，还不行显示原文；正在输出、还没写完的先显示代码。 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { Markdown } from './Markdown';
import { quoteLabels } from './mermaid';

// jsdom 量不了 SVG 文字，画图本身交给库；这里只管什么时候画、画不出来怎么办
const mermaid = vi.hoisted(() => ({
  initialize: vi.fn(),
  parse: vi.fn(async (code: string) => !code.includes('写错')),
  render: vi.fn(async (_id: string, code: string) => ({
    svg: `<svg data-lines="${code.split('\n').length}"></svg>`,
  })),
}));
vi.mock('mermaid', () => ({ default: mermaid }));

afterEach(() => vi.clearAllMocks());

const FLOW = '```mermaid\ngraph LR\n  A --> B\n```';

describe('mermaid 图', () => {
  it('mermaid 代码块画成图，用严格安全模式；别的代码块照旧', async () => {
    const { container } = render(<Markdown text={`看图：\n\n${FLOW}\n\n\`\`\`js\nlet a = 1\n\`\`\``} />);
    expect(await screen.findByRole('img', { name: '图' })).toContainHTML('<svg data-lines="2">');
    expect(mermaid.initialize).toHaveBeenCalledWith(expect.objectContaining({ securityLevel: 'strict' }));
    expect(container.querySelector('code.language-js')).toHaveTextContent('let a = 1');
  });

  it('写错了先补上引号再试；还不行显示原文并说明', async () => {
    mermaid.parse.mockImplementation(async (code: string) => code.includes('"开始（第一步）"'));
    render(<Markdown text={'```mermaid\ngraph LR\n  A[开始（第一步）] --> B\n```'} />);
    expect(await screen.findByRole('img', { name: '图' })).toBeInTheDocument();
    expect(mermaid.render).toHaveBeenCalledWith(expect.any(String), 'graph LR\n  A["开始（第一步）"] --> B');

    mermaid.parse.mockImplementation(async () => false);
    render(<Markdown text={'```mermaid\ngraph LR\n  A --> 写错\n```'} />);
    expect(await screen.findByText('没能画成图，下面是原文')).toBeInTheDocument();
    expect(screen.getByText(/A --> 写错/)).toBeInTheDocument();
    expect(mermaid.render).toHaveBeenCalledTimes(1);
  });

  it('正在输出、代码块一直到正文末尾（可能还没写完）先显示代码；后面有了别的内容或输出结束再画', async () => {
    mermaid.parse.mockImplementation(async () => true);
    const open = '```mermaid\ngraph LR\n  A --> B';
    const { rerender } = render(<Markdown text={open} streaming />);
    expect(screen.getByText(/A --> B/)).toBeInTheDocument();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(mermaid.render).not.toHaveBeenCalled();
    rerender(<Markdown text={`${FLOW}\n\n接着说明`} streaming />);
    expect(await screen.findByRole('img', { name: '图' })).toBeInTheDocument();
    rerender(<Markdown text={FLOW} />);
    await waitFor(() => expect(screen.getByRole('img', { name: '图' })).toBeInTheDocument());
  });

  it('补引号：节点文字与子图标题加上双引号，已经有引号的不动，文字里的双引号换成单引号', () => {
    expect(quoteLabels('subgraph 第一阶段\n  A[开始(1)] --> B{"已加引号"}\n  C(说 "好")')).toBe(
      'subgraph "第一阶段"\n  A["开始(1)"] --> B{"已加引号"}\n  C("说 \'好\'")',
    );
  });
});
