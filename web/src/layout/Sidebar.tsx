/** 使用端左侧浅灰侧栏：新对话、最近会话（分页）；底部是用户菜单（管理员从这里进管理后台）。 */
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { ComposeEditSquare } from '@openai/apps-sdk-ui/components/Icon';
import { NavLink } from 'react-router';
import { ConversationItem } from '../features/conversations/ConversationItem';
import { flattenPages, useConversationList } from '../features/conversations/queries';
import { Skeleton } from '../ui/Skeleton';
import { BrandMark } from '../features/brand/BrandMark';
import { useBrandHead } from '../features/brand/queries';
import { linkClass } from './nav';
import { UserMenu } from './UserMenu';

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

export function Sidebar() {
  useBrandHead();
  return (
    <nav className="kw-sidebar" aria-label="主导航">
      <div className="kw-sidebar-brand">
        <BrandMark />
      </div>
      <NavLink to="/chat" end className={linkClass}>
        <ComposeEditSquare />
        <span>新对话</span>
      </NavLink>
      <div className="kw-sidebar-scroll">
        <RecentConversations />
      </div>
      <UserMenu />
    </nav>
  );
}
