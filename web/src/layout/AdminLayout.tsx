/**
 * 管理后台外壳：和使用端分开，侧栏只放管理导航，顶部「返回工作台」。
 * 同一套登录；只有空间 owner/admin 进得来（RequireAdmin），服务端仍逐个接口判定。
 */
import { ArrowLeft } from '@openai/apps-sdk-ui/components/Icon';
import { NavLink, Outlet } from 'react-router';
import { usePendingSubmissions } from '../features/admin/skills/queries';
import { useIsWorkspaceAdmin } from '../features/auth/queries';
import { RequireAdmin } from '../features/auth/RequireAdmin';
import { BrandMark } from '../features/brand/BrandMark';
import { useBrandHead, useIsSystemAdmin } from '../features/brand/queries';
import { adminNav, linkClass, systemNav } from './nav';
import { UserMenu } from './UserMenu';

/** 「技能」菜单上的待审数：有成员提交上来的个人技能时提醒管理员去审。 */
function PendingSkillCount() {
  const admin = useIsWorkspaceAdmin();
  const pending = usePendingSubmissions(admin.data === true);
  const count = pending.data?.length ?? 0;
  if (count === 0) return null;
  return (
    <span className="kw-sidebar-count" aria-label={`${count} 个待审核`}>
      {count > 99 ? '99+' : count}
    </span>
  );
}

function AdminSidebar() {
  useBrandHead();
  const systemAdmin = useIsSystemAdmin();
  const nav = adminNav();
  const groups = systemAdmin.data ? [...nav, systemNav()] : nav;
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
                {item.to === '/admin/skills' ? <PendingSkillCount /> : null}
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
