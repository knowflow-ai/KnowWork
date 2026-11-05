/**
 * 回答下方的结果卡片：工具交给界面的那份（协议见业务服务 tools/presentation.py）。一个结果一张卡片（2026-11-04 照设计稿）：
 * - 卡片头：标题（如「销售额 · 按区域」）、下面一行灰字（时间范围、几行），右边「图 / 表」（工具给了「怎么画」时），
 *   看图时只有一列数值的，换图形的控件也在这里（见 ResultChart）；
 * - 中间：图（ResultChart）或结果表。表先列前 10 行，多的点「展开」；给界面的本来就只有前几十行，表下写一共多少行；
 * - 卡片脚：出处一行（最前面一个词如「口径」加粗），右边是扩展登记在插槽 chat.card.actions 里的操作（例如「存为可信查询」）；
 *   点开在卡片里看明细与代码，代码带「复制」。工具标了 open 的（例如 AI 临时写的查询）默认展开。
 * 表格卡片后面紧跟的出处卡片画进同一张卡片的脚；单独的出处、单独的表也各成一张。结论在回答正文里，卡片是依据。
 * 只渲染文本，不渲染 HTML。
 */
import { useState, type ReactNode } from 'react';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { ChevronDown, ChevronRight } from '@openai/apps-sdk-ui/components/Icon';
import { SegmentedControl } from '@openai/apps-sdk-ui/components/SegmentedControl';
import { slotItems } from '../../../slots/registry';
import { toast } from '../../../ui/toastStore';
import type { CardCell, ProvenanceCard, ResultCard, TableCard } from '../events/types';
import { ResultChart } from './ResultChart';

const FIRST_ROWS = 10;

interface Props {
  readonly cards: readonly ResultCard[];
  /** 这一轮用户的提问，交给卡片上的操作（例如存为可信查询时预填问法） */
  readonly question?: string;
}

interface Group {
  readonly table?: TableCard;
  readonly provenance?: ProvenanceCard;
}

/** 表格卡片和紧跟着它的出处卡片是同一个结果，合成一张卡片 */
function grouped(cards: readonly ResultCard[]): readonly Group[] {
  const out: Group[] = [];
  cards.forEach((card) => {
    const last = out.at(-1);
    if (card.type === 'provenance' && last?.table && !last.provenance)
      out[out.length - 1] = { ...last, provenance: card };
    else out.push(card.type === 'table' ? { table: card } : { provenance: card });
  });
  return out;
}

export function ResultCards({ cards, question = '' }: Props) {
  if (cards.length === 0) return null;
  return (
    <div className="kw-result-cards">
      {grouped(cards).map((group, index) => (
        // 卡片只追加、不重排，序号当键足够稳定
        <ResultCardView key={index} group={group} question={question} />
      ))}
    </div>
  );
}

function ResultCardView({ group, question }: { readonly group: Group; readonly question: string }) {
  const { table, provenance } = group;
  return (
    <section className="kw-result-card" aria-label={table?.title ?? '查询结果'}>
      {table ? <TableResult table={table} /> : null}
      {provenance ? <Provenance card={provenance} question={question} /> : null}
    </section>
  );
}

/** 结果表：带「怎么画」的默认看图（ResultChart 画卡片头与图），能切回表 */
function TableResult({ table }: { readonly table: TableCard }) {
  const [view, setView] = useState<'chart' | 'table'>(table.chart ? 'chart' : 'table');
  const viewSwitch = table.chart ? (
    <SegmentedControl aria-label="看图还是看表" size="xs" pill value={view} onChange={setView}>
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
  if (table.chart && view === 'chart') return <ResultChart table={table} chart={table.chart} head={head} />;
  return (
    <>
      {head(null)}
      <ResultTable table={table} />
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

function ResultTable({ table }: { readonly table: TableCard }) {
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
                {row.map((value, j) => (
                  <td key={table.columns[j]?.name ?? j} className={numeric[j] ? 'kw-num' : undefined}>
                    {display(value)}
                  </td>
                ))}
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

function CardActions({ card, question }: { readonly card: ProvenanceCard; readonly question: string }) {
  const registered = slotItems('chat.card.actions');
  const shown = card.actions.flatMap((action) => {
    const item = registered.find((r) => r.name === action.name);
    return item ? [{ ...action, Component: item.Component }] : [];
  });
  if (shown.length === 0) return null;
  return (
    <div className="kw-provenance-actions">
      {shown.map(({ name, ref, Component }) => (
        <Component key={`${name}:${ref}`} reference={ref} question={question} />
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

function Provenance({ card, question }: { readonly card: ProvenanceCard; readonly question: string }) {
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
        <CardActions card={card} question={question} />
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
