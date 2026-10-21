/** 工具与 MCP 页的显示规则。 */
import type { McpServer } from '../../../api/mcp';
import type { Tone } from '../../../ui/StatusDot';
import { includesText } from '../table';

export interface StatusView {
  readonly status: Tone;
  readonly text: string;
}

/** 停用优先；最近一次刷新失败时提示连接异常（工具仍按上次的清单可用）。 */
export function serverStatus(server: McpServer): StatusView {
  if (!server.enabled) return { status: 'default', text: '已停用' };
  if (server.syncError) return { status: 'warning', text: '连接异常' };
  return { status: 'success', text: '正常' };
}

/** 模型实际能用的工具数：启用且参数结构合格的。 */
export function usableCount(server: McpServer): number {
  return server.tools.filter((t) => t.enabled && !t.problem).length;
}

export function filterServers(servers: readonly McpServer[], keyword: string): readonly McpServer[] {
  if (!keyword.trim()) return servers;
  return servers.filter((s) =>
    includesText([s.title, s.name, s.url, ...s.tools.map((t) => t.rawName)], keyword),
  );
}
