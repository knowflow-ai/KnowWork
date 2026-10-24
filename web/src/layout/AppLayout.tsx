/** 主布局：左侧栏 + 右侧白底内容区。Ctrl/⌘ + Shift + S 收起或展开侧栏（照 ChatGPT）。 */
import { useEffect } from 'react';
import { Outlet } from 'react-router';
import { isSidebarShortcut, useSidebar } from '../stores/sidebar';
import { Sidebar } from './Sidebar';

export function AppLayout() {
  const toggle = useSidebar((s) => s.toggle);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (!isSidebarShortcut(event)) return;
      event.preventDefault();
      toggle();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [toggle]);
  return (
    <div className="kw-shell">
      <Sidebar />
      <main className="kw-main">
        <Outlet />
      </main>
    </div>
  );
}
