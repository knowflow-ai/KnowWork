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
  // OAuth 授权的服务登记后要登记人授权一次才拉得到工具清单
  if (server.auth === 'oauth' && !server.syncedAt) return { status: 'warning', text: '等待授权' };
  return { status: 'success', text: '正常' };
}

/** 模型实际能用的工具数：没禁用且参数结构合格的。 */
export function usableCount(server: McpServer): number {
  return server.tools.filter((t) => t.policy !== 'block' && !t.problem).length;
}

/** 之后同步才出现、管理员还没看过的工具数。 */
export const newCount = (server: McpServer): number => server.tools.filter((t) => t.isNew).length;

export function filterServers(servers: readonly McpServer[], keyword: string): readonly McpServer[] {
  if (!keyword.trim()) return servers;
  return servers.filter((s) =>
    includesText([s.title, s.name, s.url, ...s.tools.map((t) => t.rawName)], keyword),
  );
}
