/**
 * 路由表：使用端（对话、个人设置）与管理后台（/admin）是两套外壳。
 * 管理后台只给空间 owner/admin（AdminLayout 里的 RequireAdmin），服务端仍逐个接口判定。
 */
import { lazy, Suspense, type ReactNode } from 'react';
import { createBrowserRouter, Navigate, type RouteObject } from 'react-router';
import { RequireAuth } from '../features/auth/RequireAuth';
import { AdminLayout } from '../layout/AdminLayout';
import { AppLayout } from '../layout/AppLayout';
import { ADMIN_HOME } from '../layout/nav';
import { slotItems } from '../slots/registry';
import { Skeleton } from '../ui/Skeleton';

const ChatPage = lazy(() => import('../features/chat/ChatPage'));
const LoginPage = lazy(() => import('../features/auth/LoginPage'));
const InvitePage = lazy(() => import('../features/auth/InvitePage'));
const SetupPage = lazy(() => import('../features/auth/SetupPage'));
const SettingsPage = lazy(() => import('../features/settings/SettingsPage'));
const SkillsPage = lazy(() => import('../features/admin/skills/SkillsPage'));
const ChannelsPage = lazy(() => import('../features/admin/channels/ChannelsPage'));
const MembersPage = lazy(() => import('../features/admin/members/MembersPage'));
const OrgPage = lazy(() => import('../features/admin/org/OrgPage'));
const GrantsPage = lazy(() => import('../features/admin/grants/GrantsPage'));
const ModelsPage = lazy(() => import('../features/admin/models/ModelsPage'));
const KnowledgePage = lazy(() => import('../features/admin/knowledge/KnowledgePage'));
const UsagePage = lazy(() => import('../features/admin/UsagePage'));
const AuditPage = lazy(() => import('../features/admin/audit/AuditPage'));
const RunsPage = lazy(() => import('../features/admin/runs/RunsPage'));
const InsightsPage = lazy(() => import('../features/admin/insights/InsightsPage'));
const ToolsPage = lazy(() => import('../features/admin/tools/ToolsPage'));
const SystemPage = lazy(() => import('../features/admin/system/SystemPage'));
const JobsPage = lazy(() => import('../features/admin/jobs/JobsPage'));

const page = (node: ReactNode) => (
  <Suspense fallback={<Skeleton rows={6} className="kw-page-state" />}>{node}</Suspense>
);

// 扩展注册的页面插在兜底的 * 之前
const adminRoutes = (): RouteObject[] => [
  { index: true, element: <Navigate to={ADMIN_HOME} replace /> },
  { path: 'members', element: page(<MembersPage />) },
  { path: 'org', element: page(<OrgPage />) },
  { path: 'grants', element: page(<GrantsPage />) },
  { path: 'models', element: page(<ModelsPage />) },
  { path: 'knowledge', element: page(<KnowledgePage />) },
  { path: 'insights', element: page(<InsightsPage />) },
  { path: 'usage', element: page(<UsagePage />) },
  { path: 'skills', element: page(<SkillsPage />) },
  { path: 'tools', element: page(<ToolsPage />) },
  { path: 'channels', element: page(<ChannelsPage />) },
  { path: 'runs', element: page(<RunsPage />) },
  { path: 'audit', element: page(<AuditPage />) },
  { path: 'system', element: page(<SystemPage />) },
  { path: 'jobs', element: page(<JobsPage />) },
  ...slotItems('admin.routes'),
  { path: '*', element: <Navigate to={ADMIN_HOME} replace /> },
];

export const buildRoutes = (): RouteObject[] => [
  { path: '/login', element: page(<LoginPage />) },
  { path: '/invite', element: page(<InvitePage />) },
  { path: '/setup', element: page(<SetupPage />) },
  // 企业微信等机器人发来的绑定链接：要登录，不进主界面（不带侧栏）
  {
    path: '/',
    element: (
      <RequireAuth>
        <AppLayout />
      </RequireAuth>
    ),
    children: [
      { index: true, element: <Navigate to="/chat" replace /> },
      { path: 'chat', element: page(<ChatPage />) },
      { path: 'chat/:conversationId', element: page(<ChatPage />) },
      { path: 'settings', element: page(<SettingsPage />) },
      ...slotItems('app.routes'),
      { path: '*', element: <Navigate to="/chat" replace /> },
    ],
  },
  {
    path: '/admin',
    element: (
      <RequireAuth>
        <AdminLayout />
      </RequireAuth>
    ),
    children: adminRoutes(),
  },
];

export const createRouter = () => createBrowserRouter(buildRoutes());
