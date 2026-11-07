/**
 * 工具交给界面的卡片（业务服务 tools/presentation.py 的协议 v1）。业务服务投影时已校验截断过，
 * 这里再按形状认一遍：认不出的卡片丢掉，界面只渲染文本。
 */
import { isRecord } from './parse';
import type {
  CardCell,
  ProvenanceCard,
  ResultCard,
  SuggestionsCard,
  TableCard,
  TableCardColumn,
  TableChart,
  TableDrill,
} from './types';

const KINDS = new Set(['number', 'text', 'date']);
const CHART_TYPES = new Set(['line', 'bar', 'pie']);

const text = (value: unknown): string => (typeof value === 'string' ? value : '');

function cell(value: unknown): CardCell {
  if (value === null || typeof value === 'number' || typeof value === 'boolean') return value;
  return typeof value === 'string' ? value : String(value ?? '');
}

function column(value: unknown): TableCardColumn | undefined {
  if (!isRecord(value) || typeof value.name !== 'string' || value.name === '') return undefined;
  const kind = KINDS.has(text(value.kind)) ? (value.kind as TableCardColumn['kind']) : undefined;
  return {
    name: value.name,
    ...(text(value.label) ? { label: text(value.label) } : {}),
    ...(kind ? { kind } : {}),
  };
}

/** 怎么画：类型认得、列名都在表里才要（业务服务已经校验过，这里再认一遍） */
function chart(value: unknown, names: ReadonlySet<string>): TableChart | undefined {
  if (!isRecord(value) || !CHART_TYPES.has(text(value.type))) return undefined;
  const x = text(value.x);
  const y = Array.isArray(value.y) ? value.y.filter((v): v is string => typeof v === 'string') : [];
  const series = text(value.series);
  if (!names.has(x) || y.length === 0 || !y.every((v) => names.has(v) && v !== x)) return undefined;
  if (series && (!names.has(series) || series === x || y.includes(series))) return undefined;
  return { type: value.type as TableChart['type'], x, y, ...(series ? { series } : {}) };
}

/** 下钻：组件名、编号都在，列都在表里才要 */
function drill(value: unknown, names: ReadonlySet<string>): TableDrill | undefined {
  if (!isRecord(value) || !text(value.name) || !text(value.ref) || !Array.isArray(value.columns))
    return undefined;
  const columns = value.columns.filter((c): c is string => typeof c === 'string' && names.has(c));
  return columns.length > 0 ? { name: text(value.name), ref: text(value.ref), columns } : undefined;
}

function table(value: Record<string, unknown>): TableCard | undefined {
  const columns = Array.isArray(value.columns) ? value.columns.map(column) : [];
  if (columns.length === 0 || columns.some((c) => c === undefined)) return undefined;
  const width = columns.length;
  const rows = (Array.isArray(value.rows) ? value.rows : [])
    .filter((row): row is unknown[] => Array.isArray(row))
    .map((row) => Array.from({ length: width }, (_, i) => cell(row[i] ?? null)));
  const rowCount =
    typeof value.row_count === 'number' && value.row_count >= rows.length ? value.row_count : rows.length;
  const names = new Set(columns.map((c) => c?.name ?? ''));
  const drawn = chart(value.chart, names);
  const drilled = drill(value.drill, names);
  return {
    type: 'table',
    ...(text(value.title) ? { title: text(value.title) } : {}),
    ...(text(value.subtitle) ? { subtitle: text(value.subtitle) } : {}),
    columns: columns as TableCardColumn[],
    rows,
    rowCount,
    moreRows: value.more_rows === true,
    ...(drawn ? { chart: drawn } : {}),
    ...(drilled ? { drill: drilled } : {}),
  };
}

function provenance(value: Record<string, unknown>): ProvenanceCard | undefined {
  const label = text(value.label);
  const line = text(value.text);
  if (!label && !line) return undefined;
  const items = (Array.isArray(value.items) ? value.items : [])
    .filter(isRecord)
    .map((item) => ({ name: text(item.name), value: text(item.value) }))
    .filter((item) => item.name !== '');
  const code = text(value.code);
  const actions = (Array.isArray(value.actions) ? value.actions : [])
    .filter(isRecord)
    .map((action) => ({ name: text(action.name), ref: text(action.ref) }))
    .filter((action) => action.name !== '' && action.ref !== '');
  return {
    type: 'provenance',
    label,
    text: line,
    items,
    ...(code ? { code, codeLanguage: text(value.code_language) || 'text' } : {}),
    open: value.open === true,
    actions,
  };
}

function suggestions(value: Record<string, unknown>): SuggestionsCard | undefined {
  const questions = (Array.isArray(value.questions) ? value.questions : [])
    .map((question) => text(question))
    .filter((question) => question !== '');
  if (questions.length === 0) return undefined;
  const title = text(value.title);
  const line = text(value.text);
  return { type: 'suggestions', ...(title ? { title } : {}), ...(line ? { text: line } : {}), questions };
}

/** 事件里的 presentation；不是协议 v1 或一张都认不出时返回 undefined。 */
export function resultCards(value: unknown): readonly ResultCard[] | undefined {
  if (!isRecord(value) || value.version !== 1 || !Array.isArray(value.cards)) return undefined;
  const cards = value.cards
    .filter(isRecord)
    .map((card) =>
      card.type === 'table'
        ? table(card)
        : card.type === 'provenance'
          ? provenance(card)
          : card.type === 'suggestions'
            ? suggestions(card)
            : undefined,
    )
    .filter((card): card is ResultCard => card !== undefined);
  return cards.length > 0 ? cards : undefined;
}
