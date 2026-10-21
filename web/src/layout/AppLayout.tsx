/** 主布局：左侧栏 + 右侧白底内容区。 */
import { Outlet } from 'react-router';
import { Sidebar } from './Sidebar';

export function AppLayout() {
  return (
    <div className="kw-shell">
      <Sidebar />
      <main className="kw-main">
        <Outlet />
      </main>
    </div>
  );
}
