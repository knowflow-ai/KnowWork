/** 主布局：左侧栏 + 右侧白底内容区。Ctrl/⌘ + Shift + S 收起或展开侧栏，Ctrl/⌘ + K 搜索对话（照 ChatGPT）。 */
import { useEffect } from 'react';
import { Outlet } from 'react-router';
import { SearchDialog } from '../features/conversations/SearchDialog';
import { isSearchShortcut, useConversationSearch } from '../stores/conversationSearch';
import { isSidebarShortcut, useSidebar } from '../stores/sidebar';
import { Sidebar } from './Sidebar';

export function AppLayout() {
  const toggle = useSidebar((s) => s.toggle);
  const searching = useConversationSearch((s) => s.open);
  const setSearching = useConversationSearch((s) => s.setOpen);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (isSearchShortcut(event)) {
        event.preventDefault();
        setSearching(true);
        return;
      }
      if (!isSidebarShortcut(event)) return;
      event.preventDefault();
      toggle();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [toggle, setSearching]);
  return (
    <div className="kw-shell">
      <Sidebar />
      <main className="kw-main">
        <Outlet />
      </main>
      {searching ? <SearchDialog onClose={() => setSearching(false)} /> : null}
    </div>
  );
}
