/** 管理后台的导航（只有空间 owner/admin 进得来），按用途分组。 */
import {
  Analytics,
  BarChart,
  Bolt,
  BookOpen,
  Chats,
  Groups,
  History,
  Members,
  Pulse,
  Report,
  Settings,
  ShieldKey,
  Sparkle,
  Tools,
} from '@openai/apps-sdk-ui/components/Icon';
import type { ReactNode } from 'react';
import { slotItems } from '../slots/registry';

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
      { to: '/admin/knowledge', label: '知识源', icon: <BookOpen /> },
      { to: '/admin/skills', label: '技能', icon: <Bolt /> },
      { to: '/admin/tools', label: '工具与 MCP', icon: <Tools /> },
      { to: '/admin/channels', label: '渠道', icon: <Chats /> },
    ],
  },
  {
    label: '运营',
    items: [
      // 问答大盘是总览，运行记录是它点进去的明细，两个挨着
      { to: '/admin/insights', label: '问答大盘', icon: <Analytics /> },
      { to: '/admin/runs', label: '运行记录', icon: <History /> },
      { to: '/admin/usage', label: '用量', icon: <BarChart /> },
      { to: '/admin/audit', label: '审计', icon: <Report /> },
    ],
  },
];

/** 系统级设置，只有系统管理员（初始化时建的账号）看得到。模型与文档解析全平台共用，也在这里。 */
export const SYSTEM_NAV: NavGroup = {
  label: '系统',
  items: [
    { to: '/admin/models', label: '模型', icon: <Sparkle /> },
    { to: '/admin/system', label: '系统设置', icon: <Settings /> },
    { to: '/admin/jobs', label: '运行面板', icon: <Pulse /> },
  ],
};

/** 系统分组（只有系统管理员看得到）：内置项加上扩展注册的（例如空间管理），扩展的排在「系统设置」前面。 */
export function systemNav(): NavGroup {
  const extra = slotItems('system.nav');
  if (extra.length === 0) return SYSTEM_NAV;
  const items = SYSTEM_NAV.items;
  return { label: SYSTEM_NAV.label, items: [...items.slice(0, 1), ...extra, ...items.slice(1)] };
}

/** 管理后台侧栏：内置分组加上扩展注册的菜单项（插进同名分组，没有就新起一组）。 */
export function adminNav(): readonly NavGroup[] {
  const groups = ADMIN_NAV.map((group) => ({ label: group.label, items: [...group.items] }));
  for (const entry of slotItems('admin.nav')) {
    let group = groups.find((candidate) => candidate.label === entry.group);
    if (!group) {
      group = { label: entry.group, items: [] };
      groups.push(group);
    }
    const at = entry.before ?? group.items.length;
    group.items = [...group.items.slice(0, at), entry.item, ...group.items.slice(at)];
  }
  return groups;
}

export const ADMIN_HOME = '/admin/members';

export const linkClass = ({ isActive }: { isActive: boolean }) =>
  `kw-sidebar-link${isActive ? ' is-active' : ''}`;
