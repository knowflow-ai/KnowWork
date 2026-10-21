/** 管理后台的导航（只有空间 owner/admin 进得来），按用途分组。 */
import {
  BarChart,
  Bolt,
  BookOpen,
  Groups,
  History,
  Members,
  Report,
  Settings,
  ShieldKey,
  Sparkle,
  Tools,
} from '@openai/apps-sdk-ui/components/Icon';
import type { ReactNode } from 'react';

export interface NavItem {
  readonly to: string;
  readonly label: string;
  readonly icon: ReactNode;
}

export interface NavGroup {
  readonly label: string;
  readonly items: readonly NavItem[];
}

export const ADMIN_NAV: readonly NavGroup[] = [
  {
    label: '组织与权限',
    items: [
      { to: '/admin/members', label: '成员', icon: <Members /> },
      { to: '/admin/org', label: '部门与用户组', icon: <Groups /> },
      { to: '/admin/grants', label: '资源授权', icon: <ShieldKey /> },
    ],
  },
  {
    label: '能力接入',
    items: [
      { to: '/admin/models', label: '模型', icon: <Sparkle /> },
      { to: '/admin/knowledge', label: '知识源', icon: <BookOpen /> },
      { to: '/admin/skills', label: '技能', icon: <Bolt /> },
      { to: '/admin/tools', label: '工具与 MCP', icon: <Tools /> },
    ],
  },
  {
    label: '运营',
    items: [
      { to: '/admin/usage', label: '用量', icon: <BarChart /> },
      { to: '/admin/runs', label: '运行记录', icon: <History /> },
      { to: '/admin/audit', label: '审计', icon: <Report /> },
    ],
  },
];

/** 系统级设置，只有系统管理员（初始化时建的账号）看得到。 */
export const SYSTEM_NAV: NavGroup = {
  label: '系统',
  items: [{ to: '/admin/system', label: '系统设置', icon: <Settings /> }],
};

export const ADMIN_HOME = '/admin/members';

export const linkClass = ({ isActive }: { isActive: boolean }) =>
  `kw-sidebar-link${isActive ? ' is-active' : ''}`;
