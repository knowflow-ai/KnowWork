/**
 * 授权时选工具：树形选择，和选知识库同一个形态（KnowledgeBasePicker）。按 MCP 服务分组（分组本身不能选），每组第一项是
 * 「全部工具」，选它就是授 mcp__<服务名>__*（这个服务的全部工具，含以后同步进来的）；下面是服务里的各个工具，按工具原名显示。
 * 值是完整资源（`tool:<名称>`）。
 */
import { Tools } from '@openai/apps-sdk-ui/components/Icon';
import type { McpServer } from '../../../api/mcp';
import { TreeSelect, type TreeSelectNode } from '../../../ui/TreeSelect';

function serverTitle(server: McpServer): string {
  const notes = [
    server.enabled ? '' : '已停用',
    server.syncError ? `工具列表取不到：${server.syncError}` : '',
  ].filter(Boolean);
  return notes.length > 0 ? `${server.title}（${notes.join('；')}）` : server.title;
}

export function toolNodes(servers: readonly McpServer[]): readonly TreeSelectNode[] {
  return servers.map((server) => ({
    key: `server:${server.id}`,
    label: serverTitle(server),
    icon: <Tools />,
    selectable: false,
    children: [
      { key: `tool:${server.grantPattern}`, label: '全部工具', icon: <Tools />, children: [] },
      ...server.tools.map((tool) => ({
        key: `tool:${tool.name}`,
        label: tool.rawName,
        icon: <Tools />,
        children: [],
      })),
    ],
  }));
}

export function ToolPicker({
  servers,
  value,
  invalid,
  onChange,
}: {
  readonly servers: readonly McpServer[];
  readonly value: string;
  readonly invalid?: boolean;
  readonly onChange: (resource: string) => void;
}) {
  return (
    <TreeSelect
      aria-label="工具"
      nodes={toolNodes(servers)}
      value={value}
      invalid={invalid}
      placeholder={servers.length > 0 ? '选择工具' : '还没有接入 MCP 服务'}
      searchPlaceholder="搜索服务或工具"
      emptyMessage="没有匹配的选项"
      onChange={onChange}
    />
  );
}
