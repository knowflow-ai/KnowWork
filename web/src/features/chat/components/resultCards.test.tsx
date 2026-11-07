import { describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '../../../test/user';
import { renderWithApp } from '../../../test/render';
import { AssistantTurn } from './AssistantTurn';
import { ResultCards } from './ResultCards';
import { resultCards } from '../events/cards';
import type { ProvenanceCard, RunView, TableCard } from '../events/types';
import { register } from '../../../slots/registry';

// 图表库在测试环境里画不出来：换成只写说明的替身，验证的是给它的说明和切换；能点时给一个「点图上的区域2」
vi.mock('../../../ui/EChart', () => ({
  default: ({ label, onPick }: { readonly label: string; readonly onPick?: (name: string) => void }) => (
    <div role="img" aria-label={label}>
      {onPick ? (
        <button type="button" onClick={() => onPick('区域2')}>
          点图上的区域2
        </button>
      ) : null}
    </div>
  ),
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

describe('卡片上的操作换掉这张卡片', () => {
  it('操作拿到 replace：交回新的卡片协议，这张结果卡片就地换成新的，操作跟着新的查询编号', async () => {
    register('chat.card.actions', {
      name: 'adjust_example',
      Component: ({ reference, replace }) => (
        <button
          type="button"
          onClick={() =>
            replace?.({
              version: 1,
              cards: [
                {
                  type: 'table',
                  title: '订单数 · 9 月',
                  columns: [{ name: 'n', label: '订单数', kind: 'number' }],
                  rows: [[9]],
                },
                {
                  type: 'provenance',
                  label: '口径',
                  text: '条件改过 · 订单数',
                  actions: [{ name: 'adjust_example', ref: 'anql_2' }],
                },
              ],
            })
          }
        >
          改 {reference}
        </button>
      ),
    });
    const cards = resultCards({
      version: 1,
      cards: [
        {
          type: 'table',
          title: '订单数 · 8 月',
          columns: [{ name: 'n', label: '订单数', kind: 'number' }],
          rows: [[8]],
        },
        {
          type: 'provenance',
          label: '口径',
          text: '订单数',
          actions: [{ name: 'adjust_example', ref: 'anql_1' }],
        },
      ],
    });
    render(<ResultCards cards={cards ?? []} />);
    await userEvent.click(screen.getByRole('button', { name: '改 anql_1' }));
    const card = screen.getByRole('region', { name: '订单数 · 9 月' });
    expect(within(card).getByText('条件改过 · 订单数')).toBeInTheDocument();
    expect(within(card).getByRole('button', { name: '改 anql_2' })).toBeInTheDocument();
  });
});

describe('结果上点值往下钻', () => {
  const drilled = (name: string) => ({
    version: 1,
    cards: [
      {
        type: 'table',
        title: '订单数 · 按省份',
        columns: [
          { name: 'province', label: '省份' },
          { name: 'n', label: '订单数', kind: 'number' },
        ],
        rows: [['浙江', 3]],
        drill: { name, ref: 'q2', columns: ['province'] },
      },
      { type: 'provenance', label: '口径', text: '下钻过 · 订单数', actions: [] },
    ],
  });
  const register_ = (name: string) =>
    register('chat.card.drill', {
      name,
      Component: ({ reference, column, label, value, replace, onClose }) => (
        <div role="dialog" aria-label="往下钻">
          {`${reference} ${column} ${label} = ${String(value)}`}
          <button type="button" onClick={() => replace(drilled(name))}>
            按省份
          </button>
          <button type="button" onClick={onClose}>
            取消
          </button>
        </div>
      ),
    });
  const cardsOf = (extra: Record<string, unknown>, name: string) =>
    resultCards({
      version: 1,
      cards: [
        {
          type: 'table',
          title: '订单数 · 按区域',
          columns: [
            { name: 'region', label: '区域' },
            { name: 'n', label: '订单数', kind: 'number' },
          ],
          rows: [
            ['区域1', 1],
            ['区域2', 2],
          ],
          drill: { name, ref: 'q1', columns: ['region', 'gone'] },
          ...extra,
        },
      ],
    }) ?? [];

  it('表里能下钻那一列的取值能点：交给扩展登记的组件，换成钻好的结果，能返回上一步', async () => {
    register_('drill_table');
    const errors = vi.spyOn(console, 'error');
    render(<ResultCards cards={cardsOf({}, 'drill_table')} />);
    expect(screen.getByText('点带下划线的取值，可以往下钻')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /往下钻：订单数/ })).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: '往下钻：区域 = 区域2' }));
    expect(screen.getByRole('dialog', { name: '往下钻' })).toHaveTextContent('q1 region 区域 = 区域2');
    await userEvent.click(screen.getByRole('button', { name: '取消' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: '往下钻：区域 = 区域1' }));
    await userEvent.click(screen.getByRole('button', { name: '按省份' }));
    const card = screen.getByRole('region', { name: '订单数 · 按省份' });
    expect(within(card).getByRole('button', { name: '往下钻：省份 = 浙江' })).toBeInTheDocument();
    await userEvent.click(within(card).getByRole('button', { name: '返回上一步' }));
    expect(screen.getByRole('region', { name: '订单数 · 按区域' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '返回上一步' })).toBeNull();
    expect(screen.getAllByRole('table')).toHaveLength(1);
    // 表和出处并排：key 重了 React 会把它们重复画出来（实机上图画了三遍、对话框关不掉）
    expect(errors.mock.calls.flat().join(' ')).not.toMatch(/same key/);
    errors.mockRestore();
  });

  it('图上点一项也能钻（横轴那一列能下钻时）；扩展没登记那个组件时值不能点', async () => {
    register_('drill_chart');
    const { unmount } = render(
      <ResultCards cards={cardsOf({ chart: { type: 'bar', x: 'region', y: ['n'] } }, 'drill_chart')} />,
    );
    await userEvent.click(await screen.findByRole('button', { name: '点图上的区域2' }));
    expect(screen.getByText('点图上的一项或表里的取值，可以往下钻')).toBeInTheDocument();
    expect(screen.getByRole('dialog', { name: '往下钻' })).toHaveTextContent('q1 region 区域 = 区域2');
    unmount();
    render(<ResultCards cards={cardsOf({}, 'nobody_registered')} />);
    expect(screen.queryByRole('button', { name: /往下钻/ })).toBeNull();
    expect(screen.queryByText(/可以往下钻/)).toBeNull();
  });

  it('卡片协议里的下钻：组件名、编号都要有，列名对不上的去掉', () => {
    const [card] = cardsOf({}, 'drill_table');
    expect(card).toMatchObject({ drill: { name: 'drill_table', ref: 'q1', columns: ['region'] } });
    const [bare] = cardsOf({ drill: { name: 'x', columns: ['region'] } }, 'x');
    expect(bare && 'drill' in bare ? bare.drill : undefined).toBeUndefined();
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

  it('调用方能给看图的初始设置、换了告诉它（看板记住卡片上换过的图形、画哪一列、图还是表）', async () => {
    const chartTable: TableCard = {
      ...table(3),
      columns: [
        { name: 'region', label: '区域' },
        { name: 'n', label: '订单数', kind: 'number' },
        { name: 'amount', label: '金额', kind: 'number' },
      ],
      rows: [
        ['华东', 3, 300],
        ['华南', 2, 200],
      ],
      chart: { type: 'bar', x: 'region', y: ['n', 'amount'] },
    };
    const changed = vi.fn();
    const { unmount } = render(
      <ResultCards
        cards={[chartTable]}
        chartPreference={{ type: 'pie', metric: 'amount' }}
        onChartPreference={changed}
      />,
    );
    expect(await screen.findByRole('img', { name: '金额按区域的饼图' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('radio', { name: '订单数' }));
    expect(changed).toHaveBeenLastCalledWith({ type: 'pie', metric: 'n' });
    await userEvent.click(screen.getByRole('radio', { name: '柱状' }));
    expect(changed).toHaveBeenLastCalledWith({ type: 'bar', metric: 'n' });
    await userEvent.click(screen.getByRole('radio', { name: '表' }));
    expect(changed).toHaveBeenLastCalledWith({ type: 'bar', metric: 'n', view: 'table' });
    unmount();
    render(<ResultCards cards={[chartTable]} chartPreference={{ view: 'table', metric: 'nope' }} />);
    expect(screen.getByRole('table')).toBeInTheDocument();
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

describe('接着问的建议（suggestions 卡片）', () => {
  it('写明原因，问题点了就在这个对话里问；没给发问的入口（运行详情里）只列出来', async () => {
    const parsed = resultCards({
      version: 1,
      cards: [
        {
          type: 'suggestions',
          title: '这个问题查不了',
          text: '模型里没有「提成」这个指标',
          questions: ['各区域的成交金额', '各销售渠道的成交金额'],
        },
      ],
    })!;
    const asked: string[] = [];
    const { unmount } = render(<ResultCards cards={parsed} onAsk={(text) => asked.push(text)} />);
    const card = screen.getByRole('region', { name: '这个问题查不了' });
    expect(within(card).getByText('模型里没有「提成」这个指标')).toBeInTheDocument();
    await userEvent.click(within(card).getByRole('button', { name: '各销售渠道的成交金额' }));
    expect(asked).toEqual(['各销售渠道的成交金额']);
    unmount();
    render(<ResultCards cards={parsed} />);
    expect(screen.getByText('各区域的成交金额')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '各区域的成交金额' })).not.toBeInTheDocument();
  });
});
