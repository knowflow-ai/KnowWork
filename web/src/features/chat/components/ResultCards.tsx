/**
 * 回答下方的结果卡片：工具交给界面的那份（协议见业务服务 tools/presentation.py）。一个结果一张卡片（2026-11-04 照设计稿）：
 * - 卡片头：标题（如「销售额 · 按区域」）、下面一行灰字（时间范围、几行），右边「图 / 表」（工具给了「怎么画」时），
 *   看图时只有一列数值的，换图形的控件也在这里（见 ResultChart）；
 * - 中间：图（ResultChart）或结果表。表先列前 10 行，多的点「展开」；给界面的本来就只有前几十行，表下写一共多少行；
 * - 卡片脚：出处一行（最前面一个词如「口径」加粗），右边是扩展登记在插槽 chat.card.actions 里的操作（例如「存为可信查询」）；
 *   点开在卡片里看明细与代码，代码带「复制」。工具标了 open 的（例如 AI 临时写的查询）默认展开。
 * 表格卡片后面紧跟的出处卡片画进同一张卡片的脚；单独的出处、单独的表也各成一张。结论在回答正文里，卡片是依据。
 * 接着问的建议（suggestions，例如查不了时给的换个问法）单成一张：标题、一段说明、几个问题，点了就在这个对话里问；
 * 没给发问的入口（运行详情里、这一轮还在跑）就只列出来。
 * 嵌在别的页面里时（扩展的看板），调用方可以给看图的初始设置（图还是表、哪种图形、画哪一列），换了告诉它，好记在自己那边。
 * 卡片上的操作拿到 replace 时能把这张卡片换成新的结果（扩展的「改条件」重查、下钻），只换在这一页上，不写回对话记录，
 * 换过的能「返回上一步」一步步退回。表格卡片带了 drill（扩展登记了那个组件）时，图上的柱、点、扇区和表里那几列的取值能点，
 * 点了交给扩展的组件（插槽 chat.card.drill）往下钻。
 * 只渲染文本，不渲染 HTML。
 */
import { useState, type ReactNode } from 'react';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { ChevronDown, ChevronRight } from '@openai/apps-sdk-ui/components/Icon';
import { SegmentedControl } from '@openai/apps-sdk-ui/components/SegmentedControl';
import { slotItems, type CardDrillItem } from '../../../slots/registry';
import { toast } from '../../../ui/toastStore';
import { resultCards } from '../events/cards';
import type { CardCell, ProvenanceCard, ResultCard, SuggestionsCard, TableCard } from '../events/types';
import { ResultChart } from './ResultChart';
import type { ChartKind } from './chartOption';

const FIRST_ROWS = 10;

/** 看图的设置：图还是表、哪种图形、几列数值时画哪一列（列名）。都可以不给，不给按卡片默认的 */
export interface ChartPreference {
  readonly view?: 'chart' | 'table';
  readonly type?: ChartKind;
  readonly metric?: string;
}

interface Props {
  readonly cards: readonly ResultCard[];
  /** 看图的初始设置；不给按卡片默认的 */
  readonly chartPreference?: ChartPreference;
  /** 用户换了图形、画哪一列、图还是表时调；不给就只在这一页里记着 */
  readonly onChartPreference?: (preference: ChartPreference) => void;
  /** 这一轮用户的提问，交给卡片上的操作（例如存为可信查询时预填问法） */
  readonly question?: string;
  /** 在这个对话里问一句（接着问的建议点了用）；不给时建议只列出来 */
  readonly onAsk?: (text: string) => void;
}

interface Group {
  readonly table?: TableCard;
  readonly provenance?: ProvenanceCard;
  readonly suggestions?: SuggestionsCard;
}

/** 表格卡片和紧跟着它的出处卡片是同一个结果，合成一张卡片 */
function grouped(cards: readonly ResultCard[]): readonly Group[] {
  const out: Group[] = [];
  cards.forEach((card) => {
    const last = out.at(-1);
    if (card.type === 'suggestions') out.push({ suggestions: card });
    else if (card.type === 'provenance' && last?.table && !last.provenance)
      out[out.length - 1] = { ...last, provenance: card };
    else out.push(card.type === 'table' ? { table: card } : { provenance: card });
  });
  return out;
}

export function ResultCards({ cards, question = '', onAsk, chartPreference, onChartPreference }: Props) {
  if (cards.length === 0) return null;
  return (
    <div className="kw-result-cards">
      {grouped(cards).map((group, index) =>
        // 卡片只追加、不重排，序号当键足够稳定
        group.suggestions ? (
          <SuggestionsView key={index} card={group.suggestions} onAsk={onAsk} />
        ) : (
          <ResultCardView
            key={index}
            group={group}
            question={question}
            preference={chartPreference}
            onPreference={onChartPreference}
          />
        ),
      )}
    </div>
  );
}

function SuggestionsView({
  card,
  onAsk,
}: {
  readonly card: SuggestionsCard;
  readonly onAsk?: (text: string) => void;
}) {
  return (
    <section className="kw-result-card kw-suggestions-card" aria-label={card.title ?? '可以这样问'}>
      {card.title ? <span className="kw-result-title">{card.title}</span> : null}
      {card.text ? <p className="kw-secondary-text">{card.text}</p> : null}
      <div className="kw-suggestions">
        {card.questions.map((question) =>
          onAsk ? (
            <Button
              key={question}
              color="secondary"
              variant="outline"
              size="sm"
              pill
              onClick={() => onAsk(question)}
            >
              {question}
            </Button>
          ) : (
            <span key={question} className="kw-suggestion-text">
              {question}
            </span>
          ),
        )}
      </div>
    </section>
  );
}

interface PreferenceProps {
  readonly preference?: ChartPreference;
  readonly onPreference?: (preference: ChartPreference) => void;
}

function ResultCardView({
  group,
  question,
  preference,
  onPreference,
}: { readonly group: Group; readonly question: string } & PreferenceProps) {
  // 操作换过的（改条件重查、下钻）：只换在这一页上，一层层摞着好退回；换一次加一，图表的状态跟着重来
  const [shown, setShown] = useState<{ readonly stack: readonly Group[]; readonly round: number }>({
    stack: [],
    round: 0,
  });
  const { table, provenance } = shown.stack.at(-1) ?? group;
  const replaced = shown.stack.length > 0;
  const replace = (presentation: unknown) => {
    const next = grouped(resultCards(presentation) ?? []).find((g) => !g.suggestions);
    if (next) setShown((current) => ({ stack: [...current.stack, next], round: current.round + 1 }));
  };
  const back = () => setShown((current) => ({ stack: current.stack.slice(0, -1), round: current.round + 1 }));
  return (
    <section className="kw-result-card" aria-label={table?.title ?? '查询结果'}>
      {replaced ? (
        <div className="kw-result-back">
          <Button color="secondary" variant="ghost" size="2xs" onClick={back}>
            返回上一步
          </Button>
        </div>
      ) : null}
      {table ? (
        <TableResult
          // 表和出处是并排的两个孩子：key 要各不相同（同一个 key 会让 React 把它们重复画出来、换不掉）
          key={`table:${shown.round}`}
          table={table}
          // 换过的结果是临时看的：换图形、图 / 表不记回调用方那边（看板卡片记着的是原来那张的）
          preference={replaced ? undefined : preference}
          onPreference={replaced ? undefined : onPreference}
          replace={replace}
        />
      ) : null}
      {provenance ? (
        <Provenance
          key={`provenance:${shown.round}`}
          card={provenance}
          question={question}
          replace={replace}
        />
      ) : null}
    </section>
  );
}

/** 表格卡片上的下钻：扩展登记了它点名的组件才算数 */
function drillOf(
  table: TableCard,
): { readonly item: CardDrillItem; readonly columns: ReadonlySet<string> } | undefined {
  const item = table.drill
    ? slotItems('chat.card.drill').find((r) => r.name === table.drill?.name)
    : undefined;
  return item && table.drill ? { item, columns: new Set(table.drill.columns) } : undefined;
}

/** 结果表：带「怎么画」的默认看图（ResultChart 画卡片头与图），能切回表；能下钻时点值打开扩展的下钻对话框 */
function TableResult({
  table,
  preference = {},
  onPreference,
  replace,
}: { readonly table: TableCard; readonly replace: (presentation: unknown) => void } & PreferenceProps) {
  const drill = drillOf(table);
  const [picked, setPicked] = useState<{ readonly column: string; readonly value: CardCell } | null>(null);
  const pick = drill ? (column: string, value: CardCell) => setPicked({ column, value }) : undefined;
  const drillDialog =
    drill && picked && table.drill ? (
      <drill.item.Component
        reference={table.drill.ref}
        column={picked.column}
        label={table.columns.find((c) => c.name === picked.column)?.label ?? picked.column}
        value={picked.value}
        replace={(presentation) => {
          setPicked(null);
          replace(presentation);
        }}
        onClose={() => setPicked(null)}
      />
    ) : null;
  const hint = (text: string) => (drill ? <p className="kw-result-drill-hint">{text}</p> : null);
  const [view, setView] = useState<'chart' | 'table'>(table.chart ? (preference.view ?? 'chart') : 'table');
  // 图形、画哪一列由 ResultChart 换，这里攒着，换「图 / 表」时一起交出去
  const [chartPicked, setChartPicked] = useState<ChartPreference>(preference);
  const changeView = (next: 'chart' | 'table') => {
    setView(next);
    onPreference?.({ ...chartPicked, view: next });
  };
  const changeChart = (next: ChartPreference) => {
    setChartPicked(next);
    onPreference?.(view === 'chart' ? next : { ...next, view });
  };
  const viewSwitch = table.chart ? (
    <SegmentedControl aria-label="看图还是看表" size="xs" pill value={view} onChange={changeView}>
      <SegmentedControl.Option value="chart">图</SegmentedControl.Option>
      <SegmentedControl.Option value="table">表</SegmentedControl.Option>
    </SegmentedControl>
  ) : null;
  const head = (controls: ReactNode) => (
    <CardHead table={table}>
      {controls}
      {viewSwitch}
    </CardHead>
  );
  const chartX = table.chart?.x;
  if (table.chart && view === 'chart')
    return (
      <>
        <ResultChart
          table={table}
          chart={table.chart}
          head={head}
          initial={chartPicked}
          onChange={(next) => changeChart({ ...chartPicked, ...next })}
          onPick={pick && chartX && drill?.columns.has(chartX) ? (value) => pick(chartX, value) : undefined}
        />
        {hint(
          chartX && drill?.columns.has(chartX)
            ? '点图上的一项或表里的取值，可以往下钻'
            : '切到「表」，点带下划线的取值可以往下钻',
        )}
        {drillDialog}
      </>
    );
  return (
    <>
      {head(null)}
      <ResultTable table={table} pickable={drill?.columns} onPick={pick} />
      {hint('点带下划线的取值，可以往下钻')}
      {drillDialog}
    </>
  );
}

/** 卡片头：标题（工具没给就写「查询结果」），下面一行灰字（时间范围、几行），右边是控件 */
function CardHead({ table, children }: { readonly table: TableCard; readonly children: ReactNode }) {
  return (
    <header className="kw-result-head">
      <div className="kw-result-heading">
        <span className="kw-result-title">{table.title ?? '查询结果'}</span>
        <span className="kw-result-subtitle">
          {[table.subtitle, `${table.rowCount} 行`].filter(Boolean).join(' · ')}
        </span>
      </div>
      <div className="kw-result-controls">{children}</div>
    </header>
  );
}

function display(value: CardCell): string {
  if (value === null) return '—';
  if (typeof value === 'boolean') return value ? '是' : '否';
  return String(value);
}

function summary(table: TableCard): string {
  if (table.moreRows) return `结果超过上限，只取了前 ${table.rowCount} 行`;
  if (table.rowCount > table.rows.length)
    return `共 ${table.rowCount} 行，这里列出前 ${table.rows.length} 行`;
  return `共 ${table.rowCount} 行`;
}

function ResultTable({
  table,
  pickable,
  onPick,
}: {
  readonly table: TableCard;
  /** 能点着往下钻的那几列 */
  readonly pickable?: ReadonlySet<string>;
  readonly onPick?: (column: string, value: CardCell) => void;
}) {
  const [all, setAll] = useState(false);
  const rows = all ? table.rows : table.rows.slice(0, FIRST_ROWS);
  const folded = table.rows.length - rows.length;
  const numeric = table.columns.map((column) => column.kind === 'number');
  return (
    <figure className="kw-result-table" aria-label={table.title ?? '查询结果'}>
      <div className="kw-result-table-scroll">
        <table>
          <thead>
            <tr>
              {table.columns.map((column, j) => (
                <th key={column.name} className={numeric[j] ? 'kw-num' : undefined}>
                  {column.label ?? column.name}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={i}>
                {row.map((value, j) => {
                  const name = table.columns[j]?.name ?? '';
                  return (
                    <td key={name || j} className={numeric[j] ? 'kw-num' : undefined}>
                      {onPick && pickable?.has(name) ? (
                        <button
                          type="button"
                          className="kw-result-pick"
                          aria-label={`往下钻：${table.columns[j]?.label ?? name} = ${display(value)}`}
                          onClick={() => onPick(name, value)}
                        >
                          {display(value)}
                        </button>
                      ) : (
                        display(value)
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="kw-result-table-foot">
        <span>{rows.length === 0 ? '没有符合条件的数据' : summary(table)}</span>
        {folded > 0 ? (
          <Button color="secondary" variant="ghost" size="2xs" onClick={() => setAll(true)}>
            展开其余 {folded} 行
          </Button>
        ) : null}
      </div>
    </figure>
  );
}

function CardActions({
  card,
  question,
  replace,
}: {
  readonly card: ProvenanceCard;
  readonly question: string;
  readonly replace: (presentation: unknown) => void;
}) {
  const registered = slotItems('chat.card.actions');
  const shown = card.actions.flatMap((action) => {
    const item = registered.find((r) => r.name === action.name);
    return item ? [{ ...action, Component: item.Component }] : [];
  });
  if (shown.length === 0) return null;
  return (
    <div className="kw-provenance-actions">
      {shown.map(({ name, ref, Component }) => (
        <Component key={`${name}:${ref}`} reference={ref} question={question} replace={replace} />
      ))}
    </div>
  );
}

async function copy(text: string) {
  try {
    await navigator.clipboard.writeText(text);
    toast.success('已复制');
  } catch {
    toast.error('复制没有成功，请手动选中复制');
  }
}

function Provenance({
  card,
  question,
  replace,
}: {
  readonly card: ProvenanceCard;
  readonly question: string;
  readonly replace: (presentation: unknown) => void;
}) {
  const [open, setOpen] = useState(card.open);
  const expandable = card.items.length > 0 || card.code !== undefined;
  const line = (
    <>
      {card.label ? <span className="kw-provenance-label">{card.label}</span> : null}
      {card.text ? <span className="kw-provenance-text">{card.text}</span> : null}
    </>
  );
  const codeName = card.codeLanguage === 'sql' ? 'SQL' : '代码';
  return (
    <footer className="kw-result-foot">
      <div className="kw-result-foot-row">
        {expandable ? (
          <button
            type="button"
            className="kw-provenance-line"
            aria-expanded={open}
            onClick={() => setOpen(!open)}
          >
            {line}
            {open ? <ChevronDown /> : <ChevronRight />}
          </button>
        ) : (
          <div className="kw-provenance-line">{line}</div>
        )}
        <CardActions card={card} question={question} replace={replace} />
      </div>
      {expandable && open ? (
        <div className="kw-provenance-body">
          {card.items.length > 0 ? (
            <dl className="kw-provenance-items">
              {card.items.map((item) => (
                <div key={item.name}>
                  <dt>{item.name}</dt>
                  <dd>{item.value}</dd>
                </div>
              ))}
            </dl>
          ) : null}
          {card.code ? (
            <div className="kw-provenance-code">
              <div className="kw-provenance-code-head">
                <span>{codeName}</span>
                <Button
                  color="secondary"
                  variant="ghost"
                  size="2xs"
                  onClick={() => void copy(card.code ?? '')}
                >
                  复制
                </Button>
              </div>
              <pre aria-label={codeName}>
                <code>{card.code}</code>
              </pre>
            </div>
          ) : null}
        </div>
      ) : null}
    </footer>
  );
}
