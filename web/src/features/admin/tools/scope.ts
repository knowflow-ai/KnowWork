/**
 * MCP 管理的范围：空间服务（「工具与 MCP → 空间 MCP」）与企业 MCP（扩展给系统管理员的「企业」页签）用同一套列表、
 * 登记对话框与详情抽屉，不同的只有这几样：接口、列表的查询 key、「给谁用 / 发给哪些空间」那一栏、OAuth 授权回来的页面、
 * 删除时的提醒，以及系统管理员在企业页签里顺带看到的「本空间」设置（useHere）。默认是空间服务。
 */
import { createContext, useContext, type ComponentType, type ReactNode } from 'react';
import type { QueryKey } from '@tanstack/react-query';
import { workspaceMcpApi, type McpAdminApi, type McpServer } from '../../../api/mcp';
import type { RowAction } from '../common';
import { KEYS } from '../queries';
import { UsersCell, UsersRow } from './reach';

/** 列表里多一列、每行多几个操作、外加它们的对话框（企业页签里的「本空间给谁用」）。 */
export interface ExtraColumn {
  readonly title: string;
  readonly cell: (server: McpServer) => ReactNode;
  readonly actionsOf: (server: McpServer) => readonly RowAction[];
  readonly dialog: ReactNode;
}

export interface McpAdminScope {
  readonly kind: 'workspace' | 'enterprise';
  readonly api: McpAdminApi;
  readonly key: QueryKey;
  /** 列表里那一列与详情里那一行：空间服务是「给谁用」，企业 MCP 是「发给」哪些空间 */
  readonly reach: {
    readonly title: string;
    readonly Cell: ComponentType<{ readonly server: McpServer }>;
    readonly Row: ComponentType<{ readonly server: McpServer }>;
  };
  /** OAuth 授权完回到的站内页面 */
  readonly returnTo: string;
  readonly deleteConfirm: (server: McpServer) => string;
  /** 每次渲染都调（不能按条件调 hook）；没有就返回 undefined */
  readonly useExtra: () => ExtraColumn | undefined;
}

export const WORKSPACE_SCOPE: McpAdminScope = {
  kind: 'workspace',
  api: workspaceMcpApi,
  key: KEYS.mcpServers,
  reach: { title: '给谁用', Cell: UsersCell, Row: UsersRow },
  returnTo: '/admin/tools',
  deleteConfirm: (server) =>
    `删除 MCP 服务「${server.title}」？对话里将不能再调用它的工具，相关授权需要另行清理。`,
  useExtra: () => undefined,
};

export const McpScopeContext = createContext<McpAdminScope>(WORKSPACE_SCOPE);

export const useMcpScope = () => useContext(McpScopeContext);
