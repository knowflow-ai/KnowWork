/**
 * / 菜单：浮在输入框上方，和 @ 菜单同一个样子。两组：技能（按什么做法来做，这一问照常发给模型）、命令（直接执行，不发给模型）。
 * 焦点留在输入框里，点选时不抢焦点。
 */
import { Bolt, Terminal } from '@openai/apps-sdk-ui/components/Icon';
import { Link } from 'react-router';
import { MY_PLUGINS } from '../../admin/plugins/PluginsPage';
import { cx } from '../../../ui/cx';
import { pointerMoved, useScrollIntoView } from './scrollActive';
import type { MouseEvent } from 'react';
import { commandText, type SlashRow } from './slash';
import { SLASH_MENU_ID, slashOptionId, type SlashMenu as Menu } from './useSlashMenu';

const keep = (event: { preventDefault(): void }) => event.preventDefault();

const SECTIONS = {
  skill: '技能 · 按这个技能的做法来完成，这一问照常发出',
  command: '命令 · 直接执行，不发给模型',
} as const;

function RowView({
  row,
  index,
  menu,
}: {
  readonly row: SlashRow;
  readonly index: number;
  readonly menu: Menu;
}) {
  const selected = index === menu.active;
  const ref = useScrollIntoView<HTMLDivElement>(selected);
  const common = {
    ref,
    id: slashOptionId(index),
    role: 'option' as const,
    'aria-selected': selected,
    className: cx('kw-mention-row', selected && 'is-active'),
    onMouseDown: keep,
    // 鼠标真的动了才改高亮：按键让列表滚到鼠标底下时不算（见 scrollActive.ts）
    onMouseMove: (event: MouseEvent) => {
      if (pointerMoved(event) && !selected) menu.setActive(index);
    },
    onClick: () => menu.choose(row),
  };
  if (row.kind === 'skill') {
    return (
      <div {...common} aria-label={`技能 ${row.skill.name}`}>
        <span className="kw-mention-icon">
          <Bolt />
        </span>
        <span className="kw-mention-text">
          <span className="kw-mention-name">{row.skill.name}</span>
          {row.skill.description ? <span className="kw-mention-detail">{row.skill.description}</span> : null}
        </span>
        <span className="kw-mention-kind">技能</span>
      </div>
    );
  }
  const text = commandText(row.command);
  return (
    <div {...common} aria-label={`命令 /${row.command.name}`}>
      <span className="kw-mention-icon">
        <Terminal />
      </span>
      <span className="kw-mention-text">
        {/* 同官方命令菜单：有中文标题的先写标题，命令名跟在后面 */}
        <span className="kw-mention-name">
          {text.title ?? `/${row.command.name}`}
          {text.title ? <span className="kw-mention-alias">/{row.command.name}</span> : null}
        </span>
        {text.detail ? <span className="kw-mention-detail">{text.detail}</span> : null}
      </span>
      <span className="kw-mention-kind">命令</span>
    </div>
  );
}

export function SlashMenu({
  menu,
  unopened = [],
}: {
  readonly menu: Menu;
  /** 管理员设为「可选」、你还没打开的插件名：提示去「我的插件」打开（打开后新开的对话里才有它的命令与工具） */
  readonly unopened?: readonly string[];
}) {
  if (!menu.open) return null;
  return (
    <div className="kw-mention-menu" onMouseDown={keep}>
      <div className="kw-mention-title">用 / 选技能或命令 · 接着打字查找</div>
      <div id={SLASH_MENU_ID} className="kw-mention-list" role="listbox" aria-label="/ 技能与命令">
        {menu.rows.map((row, index) => [
          menu.rows[index - 1]?.kind !== row.kind ? (
            <div className="kw-mention-section" role="presentation" key={`h-${row.kind}`}>
              {SECTIONS[row.kind]}
            </div>
          ) : null,
          <RowView
            key={row.kind === 'skill' ? `s-${row.skill.name}` : `c-${row.command.name}`}
            row={row}
            index={index}
            menu={menu}
          />,
        ])}
        {menu.empty ? <div className="kw-mention-empty">{menu.empty}</div> : null}
        {menu.problem ? <div className="kw-mention-empty">{menu.problem}</div> : null}
        {menu.loading && menu.rows.length === 0 ? (
          <div className="kw-mention-empty">正在列出命令…</div>
        ) : null}
        {unopened.length > 0 ? (
          <div className="kw-mention-empty">
            可选的插件「{unopened.join('」「')}」你还没打开，到
            <Link className="kw-link" to={MY_PLUGINS} onMouseDown={keep}>
              我的插件
            </Link>
            里打开后，新开的对话就有它的命令
          </div>
        ) : null}
      </div>
    </div>
  );
}
