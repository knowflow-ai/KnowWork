/**
 * MCP 服务的工具清单抽屉：逐个工具调风险等级、审批方式与启停，改完立即生效（正在进行的对话也一样）。
 * 风险等级默认按工具自己的注解判断（声明只读的是只读，声明有破坏性的是高风险，其余是写入）。
 */
import { Badge } from '@openai/apps-sdk-ui/components/Badge';
import { Select } from '@openai/apps-sdk-ui/components/Select';
import { Switch } from '@openai/apps-sdk-ui/components/Switch';
import { Tooltip } from '@openai/apps-sdk-ui/components/Tooltip';
import { Link } from 'react-router';
import {
  APPROVALS,
  RISKS,
  type McpServer,
  type McpTool,
  type ToolApproval,
  type ToolRisk,
  type ToolSettings,
} from '../../../api/mcp';
import { formatDateTime } from '../../../utils/format';
import { DescriptionList } from '../../../ui/DescriptionList';
import { Sheet } from '../../../ui/Sheet';
import { StatusDot } from '../../../ui/StatusDot';
import { Table } from '../../../ui/Table';
import { useMcpActions, useMcpServers } from './queries';
import { serverStatus, usableCount } from './toolsModel';

const RESTORE = 'auto';

function options<T extends string>(
  base: readonly { value: T; label: string }[],
  overridden: boolean,
  restore: string,
) {
  return [...base.map((o) => ({ ...o })), ...(overridden ? [{ value: RESTORE, label: restore }] : [])];
}

function ToolName({ tool }: { readonly tool: McpTool }) {
  return (
    <div className="kw-tool-name">
      <span className="kw-labeled">
        <strong>{tool.rawName}</strong>
        {tool.problem ? (
          <Tooltip content={`${tool.problem}，不会交给模型`} compact>
            <span>
              <Badge color="warning" variant="soft" size="sm">
                不可用
              </Badge>
            </span>
          </Tooltip>
        ) : null}
      </span>
      <span className="kw-secondary-text kw-ellipsis" title={tool.description}>
        {tool.description}
      </span>
    </div>
  );
}

/** 表格里的小选择框，按「工具名 的 某设置」命名，读屏与测试都能找到它。 */
function CellSelect<T extends string>({
  label,
  value,
  choices,
  onChange,
}: {
  readonly label: string;
  readonly value: string;
  readonly choices: readonly { value: string; label: string }[];
  readonly onChange: (value: T | typeof RESTORE) => void;
}) {
  return (
    <div role="group" aria-label={label}>
      <Select
        size="sm"
        block
        value={value}
        options={[...choices]}
        onChange={(option) => onChange(option.value as T | typeof RESTORE)}
      />
    </div>
  );
}

function ToolTable({ server }: { readonly server: McpServer }) {
  const { configure } = useMcpActions();
  const save = (tool: McpTool, settings: ToolSettings) =>
    void configure.run({ server, tool: tool.name, settings });
  return (
    <Table<McpTool>
      rowKey={(tool) => tool.name}
      pageSize={false}
      rows={server.tools}
      columns={[
        { key: 'name', title: '工具', render: (tool) => <ToolName tool={tool} /> },
        {
          key: 'risk',
          title: '风险等级',
          width: 150,
          render: (tool) => (
            <CellSelect<ToolRisk>
              label={`${tool.rawName} 的风险等级`}
              value={tool.risk}
              choices={options(RISKS, tool.riskOverridden, '恢复按注解判断')}
              onChange={(risk) => save(tool, { risk })}
            />
          ),
        },
        {
          key: 'approval',
          title: '执行方式',
          width: 150,
          render: (tool) => (
            <CellSelect<ToolApproval>
              label={`${tool.rawName} 的执行方式`}
              value={tool.approval}
              choices={options(APPROVALS, tool.approvalOverridden, '恢复默认')}
              onChange={(approval) => save(tool, { approval })}
            />
          ),
        },
        {
          key: 'enabled',
          title: '启用',
          width: 70,
          render: (tool) => (
            <Switch
              checked={tool.enabled}
              label={<span className="sr-only">启用 {tool.rawName}</span>}
              onCheckedChange={(enabled) => save(tool, { enabled })}
            />
          ),
        },
      ]}
    />
  );
}

export function ServerDetail({
  serverId,
  onClose,
}: {
  readonly serverId?: string;
  readonly onClose: () => void;
}) {
  const servers = useMcpServers();
  if (serverId === undefined) return null;
  const server = servers.data?.find((s) => s.id === serverId);
  const status = server ? serverStatus(server) : null;
  return (
    <Sheet title={server?.title ?? 'MCP 服务'} width={1000} onClose={onClose}>
      {server && status ? (
        <>
          <DescriptionList
            items={[
              { label: '地址', value: server.url },
              { label: '工具名前缀', value: <code className="kw-code">{`mcp__${server.name}__`}</code> },
              { label: '状态', value: <StatusDot tone={status.status}>{status.text}</StatusDot> },
              {
                label: '可用工具',
                value: <span className="kw-num">{`${usableCount(server)} / ${server.tools.length}`}</span>,
              },
              { label: '最近刷新', value: formatDateTime(server.syncedAt) },
            ]}
          />
          {server.syncError ? (
            <p className="kw-detail-note">
              最近一次刷新失败：{server.syncError}。对话里仍按上次拉到的工具清单使用。
            </p>
          ) : null}
          <p className="kw-detail-note">
            成员默认不能使用外部工具，所有者和管理员除外。到
            <Link className="kw-link" to="/admin/grants">
              资源授权
            </Link>
            里把「{server.title}（全部工具）」授给需要的成员、用户组或部门。
          </p>
          <ToolTable server={server} />
        </>
      ) : null}
    </Sheet>
  );
}
