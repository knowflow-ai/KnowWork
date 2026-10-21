/**
 * 管理后台外壳：和使用端分开，侧栏只放管理导航，顶部「返回工作台」。
 * 同一套登录；只有空间 owner/admin 进得来（RequireAdmin），服务端仍逐个接口判定。
 */
import { ArrowLeft } from '@openai/apps-sdk-ui/components/Icon';
import { NavLink, Outlet } from 'react-router';
import { RequireAdmin } from '../features/auth/RequireAdmin';
import { BrandMark } from '../features/brand/BrandMark';
import { useBrandHead, useIsSystemAdmin } from '../features/brand/queries';
import { ADMIN_NAV, linkClass, SYSTEM_NAV } from './nav';
import { UserMenu } from './UserMenu';

function AdminSidebar() {
  useBrandHead();
  const systemAdmin = useIsSystemAdmin();
  const groups = systemAdmin.data ? [...ADMIN_NAV, SYSTEM_NAV] : ADMIN_NAV;
  return (
    <nav className="kw-sidebar" aria-label="管理导航">
      <div className="kw-admin-brand">
        <BrandMark size={22} />
        <span className="kw-admin-brand-tag">管理后台</span>
      </div>
      <NavLink to="/chat" className="kw-sidebar-link">
        <ArrowLeft />
        <span>返回工作台</span>
      </NavLink>
      <div className="kw-sidebar-scroll">
        {groups.map((group) => (
          <div key={group.label} className="kw-sidebar-section">
            <div className="kw-sidebar-label">{group.label}</div>
            {group.items.map((item) => (
              <NavLink key={item.to} to={item.to} className={linkClass}>
                {item.icon}
                <span>{item.label}</span>
              </NavLink>
            ))}
          </div>
        ))}
      </div>
      <UserMenu inAdmin />
    </nav>
  );
}

export function AdminLayout() {
  return (
    <div className="kw-shell">
      <AdminSidebar />
      <main className="kw-main">
        <RequireAdmin>
          <Outlet />
        </RequireAdmin>
      </main>
    </div>
  );
}
