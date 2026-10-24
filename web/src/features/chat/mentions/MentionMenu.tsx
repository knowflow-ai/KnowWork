/**
 * @ 菜单：浮在输入框上方。分组标题写「类型 · 选了会怎样」，每一项左边是类型图标、下面一行小字写在哪里，
 * 让用户一眼分清是知识库、目录、文件、标签还是技能。焦点留在输入框里，点选时不抢焦点。
 */
import { ChevronLeft, ChevronRight } from '@openai/apps-sdk-ui/components/Icon';
import { cx } from '../../../ui/cx';
import { MENTION_GROUPS, MENTION_KINDS, type MentionGroup } from './kinds';
import { MENTION_MENU_ID, optionId, type MentionMenu as Menu, type Row } from './useMentionMenu';

const groupOf = (group: MentionGroup) => MENTION_GROUPS.find((g) => g.group === group)!;
const keep = (event: { preventDefault(): void }) => event.preventDefault();

function RowView({ row, index, menu }: { readonly row: Row; readonly index: number; readonly menu: Menu }) {
  const selected = index === menu.active;
  const common = {
    id: optionId(index),
    role: 'option' as const,
    'aria-selected': selected,
    className: cx('kw-mention-row', selected && 'is-active'),
    onMouseDown: keep,
    onMouseEnter: () => menu.setActive(index),
    onClick: () => menu.choose(row),
  };
  if (row.kind === 'group') {
    const meta = groupOf(row.group);
    return (
      <div {...common}>
        <span className="kw-mention-icon">{meta.icon}</span>
        <span className="kw-mention-text">
          <span className="kw-mention-name">{meta.label}</span>
          <span className="kw-mention-detail">{meta.effect}</span>
        </span>
        {row.count !== undefined ? <span className="kw-mention-count">{row.count}</span> : null}
        <ChevronRight className="kw-mention-arrow" />
      </div>
    );
  }
  if (row.kind === 'more') {
    return (
      <div {...common}>
        <span className="kw-mention-more">查看全部 {row.total} 项</span>
        <ChevronRight className="kw-mention-arrow" />
      </div>
    );
  }
  const { mention, detail } = row.candidate;
  const kind = MENTION_KINDS[mention.type];
  return (
    <div {...common} aria-label={`${kind.label} ${mention.name}`}>
      <span className="kw-mention-icon">{kind.icon}</span>
      <span className="kw-mention-text">
        <span className="kw-mention-name">{mention.name}</span>
        {detail ? <span className="kw-mention-detail">{detail}</span> : null}
      </span>
      <span className="kw-mention-kind">{kind.label}</span>
    </div>
  );
}

/** 直接输入查找时，每组前面一行标题：类型 · 选了会怎样。 */
function header(row: Row, previous: Row | undefined, menu: Menu) {
  if (menu.group !== null || row.kind === 'group' || previous?.group === row.group) return null;
  const meta = groupOf(row.group);
  return (
    <div className="kw-mention-section" role="presentation" key={`h-${row.group}`}>
      {meta.label} · {meta.effect}
    </div>
  );
}

export function MentionMenu({ menu }: { readonly menu: Menu }) {
  if (!menu.open) return null;
  const current = menu.group ? groupOf(menu.group) : null;
  return (
    <div className="kw-mention-menu" onMouseDown={keep}>
      {current ? (
        <button type="button" className="kw-mention-back" onMouseDown={keep} onClick={menu.leave}>
          <ChevronLeft />
          <span className="kw-mention-name">{current.label}</span>
          <span className="kw-mention-detail">{current.effect}</span>
        </button>
      ) : (
        <div className="kw-mention-title">用 @ 点名这一问的范围或技能 · 接着打字查找，→ 进入分组</div>
      )}
      <div id={MENTION_MENU_ID} className="kw-mention-list" role="listbox" aria-label="@ 点名">
        {menu.rows.map((row, index) => [
          header(row, menu.rows[index - 1], menu),
          <RowView key={`${row.kind}-${row.group}-${index}`} row={row} index={index} menu={menu} />,
        ])}
        {menu.empty ? <div className="kw-mention-empty">{menu.empty}</div> : null}
        {menu.note ? <div className="kw-mention-empty">{menu.note}</div> : null}
        {menu.loading && menu.rows.length === 0 ? <div className="kw-mention-empty">正在查找…</div> : null}
      </div>
    </div>
  );
}
