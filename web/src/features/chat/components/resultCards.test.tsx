import { describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '../../../test/user';
import { renderWithApp } from '../../../test/render';
import { AssistantTurn } from './AssistantTurn';
import { ResultCards } from './ResultCards';
import { resultCards } from '../events/cards';
import type { ProvenanceCard, RunView, TableCard } from '../events/types';
import { register } from '../../../slots/registry';

// 图表库在测试环境里画不出来：换成只写说明的替身，验证的是给它的说明和切换
vi.mock('../../../ui/EChart', () => ({
  default: ({ label }: { readonly label: string }) => <div role="img" aria-label={label} />,
}));

const table = (rows: number, rowCount = rows): TableCard => ({
  type: 'table',
  columns: [
    { name: 'region', label: '区域' },
    { name: 'n', label: '订单数', kind: 'number' },
  ],
  rows: Array.from({ length: rows }, (_, i) => [`区域${i + 1}`, i + 1]),
  rowCount,
  moreRows: false,
});

const sql: ProvenanceCard = {
  type: 'provenance',
  label: '口径',
  text: '订单数 · 2026-07-01 至 2026-09-30',
  items: [{ name: '过滤', value: '剔除测试订单' }],
  code: 'SELECT region_name, COUNT(*) FROM orders',
  codeLanguage: 'sql',
  open: false,
  actions: [],
};

describe('ResultCards', () => {
  it('结果表用列标签作表头，先列前 10 行，点展开看其余的；表下写一共多少行', async () => {
    render(<ResultCards cards={[table(12, 40)]} />);
    const figure = screen.getByRole('figure', { name: '查询结果' });
    expect(
      within(figure)
        .getAllByRole('columnheader')
        .map((h) => h.textContent),
    ).toEqual(['区域', '订单数']);
    expect(within(figure).getAllByRole('row')).toHaveLength(11);
    expect(screen.getByText('共 40 行，这里列出前 12 行')).toBeInTheDocument();
    expect(within(figure).getAllByRole('cell')[1]).toHaveClass('kw-num');
    await userEvent.click(screen.getByRole('button', { name: '展开其余 2 行' }));
    expect(within(figure).getAllByRole('row')).toHaveLength(13);
  });

  it('空结果写明没有数据；结果被上限截过的说清楚', () => {
    const { rerender } = render(<ResultCards cards={[table(0)]} />);
    expect(screen.getByText('没有符合条件的数据')).toBeInTheDocument();
    rerender(<ResultCards cards={[{ ...table(3, 2000), moreRows: true }]} />);
    expect(screen.getByText('结果超过上限，只取了前 2000 行')).toBeInTheDocument();
  });

  it('出处默认一行，点开看明细与代码；标了 open 的默认展开；没有明细的不能点', async () => {
    const { rerender } = render(<ResultCards cards={[sql]} />);
    const toggle = screen.getByRole('button', { name: /口径/ });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByText(/SELECT region_name/)).toBeNull();
    await userEvent.click(toggle);
    expect(screen.getByText('剔除测试订单')).toBeInTheDocument();
    expect(screen.getByLabelText('SQL')).toHaveTextContent('SELECT region_name, COUNT(*) FROM orders');
    rerender(<ResultCards cards={[{ ...sql, open: true }]} />);
    expect(screen.getByLabelText('SQL')).toBeInTheDocument();
    rerender(<ResultCards cards={[{ ...sql, items: [], code: undefined }]} />);
    expect(screen.queryByRole('button')).toBeNull();
    expect(screen.getByText('口径')).toBeInTheDocument();
  });

  it('表和紧跟着的出处合成一张卡片：头上标题与灰字（时间范围、几行），脚上是出处；代码能复制', async () => {
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    const titled = { ...table(3), title: '订单数 · 按区域', subtitle: '2026-07-01 至 2026-09-30' };
    render(<ResultCards cards={[titled, sql, sql]} />);
    const [card, alone] = screen.getAllByRole('region');
    expect(screen.getAllByRole('region')).toHaveLength(2);
    expect(card).toHaveAccessibleName('订单数 · 按区域');
    expect(within(card!).getByText('2026-07-01 至 2026-09-30 · 3 行')).toBeInTheDocument();
    expect(within(card!).getByRole('table')).toBeInTheDocument();
    expect(within(alone!).queryByRole('table')).toBeNull();
    await userEvent.click(within(card!).getByRole('button', { name: /口径/ }));
    await userEvent.click(within(card!).getByRole('button', { name: '复制' }));
    expect(writeText).toHaveBeenCalledWith('SELECT region_name, COUNT(*) FROM orders');
  });

  it('只认协议里的形状：认不出的卡片丢掉，单元格只留文本', () => {
    expect(resultCards({ version: 2, cards: [] })).toBeUndefined();
    expect(resultCards({ version: 1, cards: [{ type: 'script', src: 'x' }] })).toBeUndefined();
    const [card] =
      resultCards({
        version: 1,
        cards: [
          {
            type: 'table',
            columns: [{ name: 'a' }, { name: 'b' }],
            rows: [['<b>x</b>', { o: 1 }], [1]],
            row_count: 1,
          },
        ],
      }) ?? [];
    expect(card).toEqual({
      type: 'table',
      columns: [{ name: 'a' }, { name: 'b' }],
      rows: [
        ['<b>x</b>', '[object Object]'],
        [1, null],
      ],
      rowCount: 2,
      moreRows: false,
    });
  });
});

describe('卡片上的操作', () => {
  it('按操作名找扩展登记的组件，交给它 ref 与这一轮的提问；没登记的操作不显示', () => {
    register('chat.card.actions', {
      name: 'save_example',
      Component: ({ reference, question }) => (
        <button type="button">
          存下 {reference} · {question}
        </button>
      ),
    });
    const cards = resultCards({
      version: 1,
      cards: [
        {
          type: 'provenance',
          label: '口径',
          text: '订单数',
          actions: [
            { name: 'save_example', ref: 'anql_1' },
            { name: 'unknown_action', ref: 'x' },
          ],
        },
      ],
    });
    render(<ResultCards cards={cards ?? []} question="上月订单数" />);
    expect(screen.getByRole('button', { name: '存下 anql_1 · 上月订单数' })).toBeInTheDocument();
    expect(screen.getAllByRole('button')).toHaveLength(1);
  });
});

describe('回答里的卡片', () => {
  it('画在回答正文之后，按步骤顺序；没结束的步骤不画', () => {
    const run: RunView = {
      runId: 'run-1',
      status: 'completed',
      startedAt: 0,
      endedAt: 1000,
      notes: [],
      thinking: [],
      answer: '华东订单最多，共 5 单。',
      problems: [],
      asked: [],
      trail: [],
      steps: [
        {
          callId: 'c1',
          kind: 'tool',
          name: 'show_result',
          arguments: '{}',
          status: 'done',
          cards: [table(2), sql],
        },
        {
          callId: 'c2',
          kind: 'tool',
          name: 'show_result',
          arguments: '{}',
          status: 'running',
          cards: [table(1)],
        },
      ],
    };
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
    const answer = screen.getByText('华东订单最多，共 5 单。');
    const figures = screen.getAllByRole('figure', { name: '查询结果' });
    expect(figures).toHaveLength(1);
    expect(answer.compareDocumentPosition(figures[0]!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByRole('button', { name: /口径/ })).toBeInTheDocument();
  });

  it('这一轮还没结束时不出卡片（结论还在往外写，卡片早出会被插在上面的结论往下推），结束后接在正文下面', () => {
    const running: RunView = {
      runId: 'run-2',
      status: 'running',
      startedAt: 0,
      notes: [],
      thinking: [],
      answer: '华东订单最多',
      problems: [],
      asked: [],
      trail: [],
      steps: [
        {
          callId: 'c1',
          kind: 'tool',
          name: 'show_result',
          arguments: '{}',
          status: 'done',
          cards: [table(2), sql],
        },
      ],
    };
    const turn = (run: RunView) => (
      <AssistantTurn
        run={run}
        approvals={[]}
        questions={[]}
        artifacts={[]}
        onDecide={vi.fn()}
        onAnswer={vi.fn(async () => true)}
        busy={false}
      />
    );
    const { rerender } = renderWithApp(turn(running));
    expect(screen.queryByRole('region', { name: '查询结果' })).toBeNull();
    rerender(turn({ ...running, status: 'completed', endedAt: 1000 }));
    expect(screen.getByRole('region', { name: '查询结果' })).toBeInTheDocument();
  });

  it('带「怎么画」的表默认显示图，可以切回表；几列数值时切换画哪一列、换图形；只带了前几行时写明', async () => {
    const chartTable: TableCard = {
      ...table(3, 40),
      columns: [
        { name: 'region', label: '区域' },
        { name: 'n', label: '订单数', kind: 'number' },
        { name: 'amount', label: '金额', kind: 'number' },
      ],
      rows: [
        ['华东', 3, 300],
        ['华南', 2, 200],
        ['华北', 1, 100],
      ],
      chart: { type: 'bar', x: 'region', y: ['n', 'amount'] },
    };
    render(<ResultCards cards={[chartTable]} />);
    expect(await screen.findByRole('img', { name: '订单数按区域的柱状图' })).toBeInTheDocument();
    // 几个指标时换图形的控件在图上面那一排，不在卡片头里
    expect(within(screen.getByRole('banner')).queryByRole('radiogroup', { name: '图形' })).toBeNull();
    expect(screen.getByText('图里只画了前 3 行')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('radio', { name: '金额' }));
    expect(await screen.findByRole('img', { name: '金额按区域的柱状图' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('radio', { name: '饼图' }));
    expect(await screen.findByRole('img', { name: '金额按区域的饼图' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('radio', { name: '表' }));
    expect(screen.getByRole('table')).toBeInTheDocument();
    expect(screen.queryByRole('img')).toBeNull();
  });

  it('只有一列数值时换图形的控件在卡片头里；按组分线时图上面一排是图例', async () => {
    const single: TableCard = { ...table(3), chart: { type: 'bar', x: 'region', y: ['n'] } };
    const { unmount } = render(<ResultCards cards={[single]} />);
    expect(await screen.findByRole('img', { name: '订单数按区域的柱状图' })).toBeInTheDocument();
    const head = screen.getByRole('banner');
    expect(within(head).getByRole('radiogroup', { name: '图形' })).toBeInTheDocument();
    expect(within(head).getByRole('radiogroup', { name: '看图还是看表' })).toBeInTheDocument();
    unmount();
    const lines: TableCard = {
      type: 'table',
      columns: [
        { name: 'month', label: '月份', kind: 'date' },
        { name: 'region', label: '区域' },
        { name: 'n', label: '订单数', kind: 'number' },
      ],
      rows: [
        ['2026-01', '华东', 3],
        ['2026-01', '华南', 2],
        ['2026-02', '华东', 4],
      ],
      rowCount: 3,
      moreRows: false,
      chart: { type: 'line', x: 'month', y: ['n'], series: 'region' },
    };
    render(<ResultCards cards={[lines]} />);
    expect(await screen.findByRole('img', { name: '订单数按月份的折线图' })).toBeInTheDocument();
    const legend = screen.getByRole('list', { name: '图例' });
    expect(
      within(legend)
        .getAllByRole('listitem')
        .map((li) => li.textContent),
    ).toEqual(['华东', '华南']);
    expect(within(screen.getByRole('banner')).queryByRole('radiogroup', { name: '图形' })).toBeNull();
  });

  it('卡片协议里的「怎么画」：列名对不上就不带图', () => {
    const base = {
      type: 'table',
      columns: [{ name: 'region' }, { name: 'n', kind: 'number' }],
      rows: [['华东', 1]],
    };
    const parsed = resultCards({
      version: 1,
      cards: [{ ...base, chart: { type: 'bar', x: 'region', y: ['n'] } }],
    });
    expect(parsed?.[0]).toMatchObject({ chart: { type: 'bar', x: 'region', y: ['n'] } });
    const wrong = resultCards({
      version: 1,
      cards: [{ ...base, chart: { type: 'bar', x: 'nope', y: ['n'] } }],
    });
    expect(wrong?.[0]).not.toHaveProperty('chart');
  });
});
