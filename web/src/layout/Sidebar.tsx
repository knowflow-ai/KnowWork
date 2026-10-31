/**
 * 使用端左侧浅灰侧栏：新对话、技能、扩展注册的入口（app.nav）、最近会话（分页）；底部是用户菜单（管理员从这里进管理后台）。
 * 顶上一行右边是「搜索对话」（放大镜，或 Ctrl/⌘ + K）和「收起侧栏」，照 ChatGPT；收起时放大镜在「新对话」下面。
 * 照 ChatGPT 可以收起：右上角的按钮（或 Ctrl/⌘ + Shift + S）收成一条只有图标的窄栏，最近会话不显示，
 * 入口的名字悬停时显示；窄栏顶上是产品图标，悬停变成「展开侧栏」。收起与否只记在本浏览器里。
 */
import type { ReactNode } from 'react';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { Bolt, ComposeEditSquare, Search, Sidebar as SidebarIcon } from '@openai/apps-sdk-ui/components/Icon';
import { Tooltip } from '@openai/apps-sdk-ui/components/Tooltip';
import { NavLink, useMatch } from 'react-router';
import { DEFAULT_MARK } from '../api/system';
import { ConversationItem } from '../features/conversations/ConversationItem';
import { flattenPages, useConversationList } from '../features/conversations/queries';
import { Skeleton } from '../ui/Skeleton';
import { cx } from '../ui/cx';
import { BrandMark } from '../features/brand/BrandMark';
import { useBrand, useBrandHead } from '../features/brand/queries';
import { slotItems } from '../slots/registry';
import { useConversationSearch } from '../stores/conversationSearch';
import { useSidebar } from '../stores/sidebar';
import { UserMenu } from './UserMenu';

const MAC = navigator.platform.toLowerCase().includes('mac');
const SHORTCUT = MAC ? '⌘ ⇧ S' : 'Ctrl Shift S';
const SEARCH_SHORTCUT = MAC ? '⌘ K' : 'Ctrl K';

function RecentConversations() {
  const list = useConversationList();
  const items = flattenPages(list.data?.pages);
  return (
    <div className="kw-sidebar-section">
      <div className="kw-sidebar-label">最近会话</div>
      {list.isPending ? <Skeleton rows={3} className="kw-sidebar-skeleton" /> : null}
      {list.isError ? <div className="kw-sidebar-empty">会话列表加载失败</div> : null}
      {list.isSuccess && items.length === 0 ? <div className="kw-sidebar-empty">还没有会话</div> : null}
      {items.map((item) => (
        <ConversationItem key={item.id} item={item} />
      ))}
      {list.hasNextPage ? (
        <Button
          color="secondary"
          variant="ghost"
          size="sm"
          className="kw-sidebar-more"
          loading={list.isFetchingNextPage}
          onClick={() => void list.fetchNextPage()}
        >
          加载更多
        </Button>
      ) : null}
    </div>
  );
}

/**
 * 一个入口：展开时图标加名字；收起时只有图标，名字放在悬停提示里（读屏照样念名字）。
 * 选中样式自己算成字符串：提示组件经 Radix Slot 合并 className 时按字符串拼接，NavLink 的函数式 className
 * 会被拼成一段函数源码，入口的样式全丢（收起后挤成一团，2026-10-23 实测）。
 */
function Entry({
  to,
  end = false,
  icon,
  label,
  collapsed,
}: {
  readonly to: string;
  readonly end?: boolean;
  readonly icon: ReactNode;
  readonly label: string;
  readonly collapsed: boolean;
}) {
  const active = useMatch({ path: to, end }) !== null;
  const link = (
    <NavLink
      to={to}
      end={end}
      className={cx('kw-sidebar-link', active && 'is-active')}
      aria-label={collapsed ? label : undefined}
    >
      {icon}
      {collapsed ? null : <span>{label}</span>}
    </NavLink>
  );
  return collapsed ? (
    <Tooltip content={label} side="right" compact>
      {link}
    </Tooltip>
  ) : (
    link
  );
}

/** 顶上一行：展开时是品牌字样和「收起侧栏」；收起时是产品图标，悬停变成「展开侧栏」。 */
function openSearch() {
  useConversationSearch.getState().setOpen(true);
}

function Head({ collapsed, onToggle }: { readonly collapsed: boolean; readonly onToggle: () => void }) {
  const brand = useBrand();
  if (collapsed) {
    return (
      <Tooltip content={`展开侧栏（${SHORTCUT}）`} side="right" compact>
        <button type="button" className="kw-sidebar-open" aria-label="展开侧栏" onClick={onToggle}>
          <img className="kw-sidebar-open-mark" src={brand.logoUrl ?? DEFAULT_MARK} alt="" />
          <SidebarIcon className="kw-sidebar-open-icon" />
        </button>
      </Tooltip>
    );
  }
  return (
    <div className="kw-sidebar-brand">
      <BrandMark />
      <div className="kw-sidebar-tools">
        <Tooltip content={`搜索对话（${SEARCH_SHORTCUT}）`} compact>
          <Button
            color="secondary"
            variant="ghost"
            size="sm"
            uniform
            aria-label="搜索对话"
            onClick={openSearch}
          >
            <Search />
          </Button>
        </Tooltip>
        <Tooltip content={`收起侧栏（${SHORTCUT}）`} compact>
          <Button
            color="secondary"
            variant="ghost"
            size="sm"
            uniform
            aria-label="收起侧栏"
            onClick={onToggle}
          >
            <SidebarIcon />
          </Button>
        </Tooltip>
      </div>
    </div>
  );
}

export function Sidebar() {
  useBrandHead();
  const collapsed = useSidebar((s) => s.collapsed);
  const toggle = useSidebar((s) => s.toggle);
  return (
    <nav className={cx('kw-sidebar', collapsed && 'is-collapsed')} aria-label="主导航">
      <Head collapsed={collapsed} onToggle={toggle} />
      <Entry to="/chat" end icon={<ComposeEditSquare />} label="新对话" collapsed={collapsed} />
      {collapsed ? (
        <Tooltip content={`搜索对话（${SEARCH_SHORTCUT}）`} side="right" compact>
          <button type="button" className="kw-sidebar-link" aria-label="搜索对话" onClick={openSearch}>
            <Search />
          </button>
        </Tooltip>
      ) : null}
      <Entry to="/skills" icon={<Bolt />} label="技能" collapsed={collapsed} />
      {slotItems('app.nav').map((item) => (
        <Entry key={item.to} to={item.to} icon={item.icon} label={item.label} collapsed={collapsed} />
      ))}
      {collapsed ? (
        <div className="kw-sidebar-spacer" />
      ) : (
        <div className="kw-sidebar-scroll">
          <RecentConversations />
        </div>
      )}
      <UserMenu compact={collapsed} />
    </nav>
  );
}
