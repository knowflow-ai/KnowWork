/**
 * 搜索对话（照 ChatGPT）：顶上是输入框；没输入时第一行是「新对话」，下面是最近的对话按今天、昨天、前 7 天……分组；
 * 输入后列出标题或问过的话里有这个词的对话，命中的那句问话放在标题下面、命中处加粗。↑↓ 选、回车打开、Esc 关。
 * 回答的正文不搜（服务端只存了每轮问的话，见 conversations/search.py）。
 */
import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from 'react';
import { Chat, ComposeEditSquare, Search } from '@openai/apps-sdk-ui/components/Icon';
import { Input } from '@openai/apps-sdk-ui/components/Input';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router';
import { searchConversations, type ConversationHit, type ConversationSummary } from '../../api/conversations';
import { Dialog } from '../../ui/Dialog';
import { cx } from '../../ui/cx';
import { flattenPages, useConversationList } from './queries';
import { groupByTime, splitHits } from './search';

const DEBOUNCE_MS = 200;

function useDebounced(value: string): string {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [value]);
  return debounced;
}

interface Row {
  readonly key: string;
  readonly to: string;
  readonly icon?: ReactNode;
  readonly title: string;
  readonly snippet?: string;
}

const rowOf = (item: ConversationSummary | ConversationHit): Row => ({
  key: item.id,
  to: `/chat/${encodeURIComponent(item.id)}`,
  title: item.title || '未命名对话',
  snippet: 'snippet' in item && item.snippet ? item.snippet : undefined,
});

function Marked({ text, query }: { readonly text: string; readonly query: string }) {
  return (
    <>
      {splitHits(text, query).map((part, index) =>
        part.hit ? (
          <strong key={index} className="kw-search-hit">
            {part.text}
          </strong>
        ) : (
          <span key={index}>{part.text}</span>
        ),
      )}
    </>
  );
}

export function SearchDialog({ onClose }: { readonly onClose: () => void }) {
  const navigate = useNavigate();
  const [query, setQuery] = useState('');
  const [now] = useState(() => Date.now()); // 分组按打开搜索框的那一刻算
  const term = useDebounced(query.trim());
  const recent = useConversationList();
  const found = useQuery({
    queryKey: ['conversations', 'search', term],
    queryFn: () => searchConversations(term),
    enabled: term.length > 0,
  });
  const groups = useMemo(() => {
    if (term) return [{ label: '', rows: (found.data ?? []).map(rowOf) }];
    const newChat: Row = { key: 'new', to: '/chat', icon: <ComposeEditSquare />, title: '新对话' };
    return [
      { label: '', rows: [newChat] },
      ...groupByTime(flattenPages(recent.data?.pages), now).map((g) => ({
        label: g.label,
        rows: g.items.map(rowOf),
      })),
    ];
  }, [term, found.data, recent.data, now]);
  const rows = groups.flatMap((g) => g.rows);
  const [picked, setActive] = useState(0);
  const active = Math.min(picked, Math.max(rows.length - 1, 0));
  const list = useRef<HTMLUListElement>(null);
  useEffect(() => {
    list.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: 'nearest' });
  }, [active]);

  const open = (row: Row | undefined) => {
    if (!row) return;
    onClose();
    navigate(row.to);
  };
  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      const step = event.key === 'ArrowDown' ? 1 : -1;
      setActive((current) => (rows.length ? (current + step + rows.length) % rows.length : 0));
    } else if (event.key === 'Enter' && !event.nativeEvent.isComposing) {
      event.preventDefault();
      open(rows[active]);
    }
  };

  const empty = term && found.isSuccess && rows.length === 0;
  return (
    <Dialog title="搜索对话" hideTitle size="md" onClose={onClose} className="kw-search-dialog">
      <Input
        autoFocus
        aria-label="搜索对话"
        role="combobox"
        aria-expanded
        aria-controls="kw-search-results"
        aria-activedescendant={rows[active] ? `kw-search-${rows[active].key}` : undefined}
        placeholder="搜索对话…"
        variant="soft"
        startAdornment={<Search />}
        value={query}
        onChange={(event) => {
          setQuery(event.target.value);
          setActive(0);
        }}
        onKeyDown={onKeyDown}
      />
      <ul id="kw-search-results" ref={list} className="kw-search-results" role="listbox" aria-label="对话">
        {groups.map((group) =>
          group.rows.length === 0 ? null : (
            <li key={group.label || 'top'} role="presentation">
              {group.label ? <div className="kw-search-group">{group.label}</div> : null}
              <ul role="presentation">
                {group.rows.map((row) => {
                  const index = rows.indexOf(row);
                  return (
                    <li
                      key={row.key}
                      id={`kw-search-${row.key}`}
                      role="option"
                      aria-selected={index === active}
                      className={cx('kw-search-row', index === active && 'is-active')}
                      onMouseEnter={() => setActive(index)}
                      onClick={() => open(row)}
                    >
                      {row.icon ?? <Chat aria-hidden />}
                      <span className="kw-search-row-text">
                        <span className="kw-search-row-title">
                          <Marked text={row.title} query={term} />
                        </span>
                        {row.snippet ? (
                          <span className="kw-search-row-snippet">
                            <Marked text={row.snippet} query={term} />
                          </span>
                        ) : null}
                      </span>
                    </li>
                  );
                })}
              </ul>
            </li>
          ),
        )}
      </ul>
      {empty ? <p className="kw-search-empty">没有找到相关的对话</p> : null}
      {found.isError ? <p className="kw-search-empty">搜索没有成功，请稍后再试</p> : null}
    </Dialog>
  );
}
