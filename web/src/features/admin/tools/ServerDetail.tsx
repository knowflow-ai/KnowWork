/**
 * MCP 服务的详情抽屉：给谁用（默认全体成员，在这里收窄；企业 MCP 是发给哪些空间，见 scope.ts），每个工具一个三档——
 * 允许、需确认、禁用，按只读与写入分组。
 * 改完立即生效（正在进行的对话也一样）。第一次登记时照对方的注解预填（只读的允许、其余需确认）；之后同步才出现的工具
 * 一律需确认并标「新」，不照对方的标注放开。
 */
import { Badge } from '@openai/apps-sdk-ui/components/Badge';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { SegmentedControl } from '@openai/apps-sdk-ui/components/SegmentedControl';
import { Tooltip } from '@openai/apps-sdk-ui/components/Tooltip';
import { POLICIES, type McpServer, type McpTool, type ToolGroup, type ToolPolicy } from '../../../api/mcp';
import { formatDateTime } from '../../../utils/format';
import { DescriptionList } from '../../../ui/DescriptionList';
import { Sheet } from '../../../ui/Sheet';
import { StatusDot } from '../../../ui/StatusDot';
import { Table } from '../../../ui/Table';
import { useMcpLogin } from './login';
import { useMcpActions, useMcpServers } from './queries';
import { useMcpScope } from './scope';
import { newCount, serverStatus, usableCount } from './toolsModel';

const GROUPS: readonly { readonly value: ToolGroup; readonly title: string; readonly other: string }[] = [
  { value: 'read', title: '只读工具', other: '挪到写入组' },
  { value: 'write', title: '写入工具', other: '挪到只读组' },
];

function ToolName({ tool }: { readonly tool: McpTool }) {
  return (
    <div className="kw-tool-name">
      <span className="kw-labeled">
        <strong>{tool.rawName}</strong>
        {tool.isNew ? (
          <Tooltip content="之后同步才出现的工具，先设成需确认，看过后再定" compact>
            <span>
              <Badge color="discovery" variant="soft" size="sm">
                新
              </Badge>
            </span>
          </Tooltip>
        ) : null}
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

function ToolGroupTable({
  server,
  group,
}: {
  readonly server: McpServer;
  readonly group: (typeof GROUPS)[number];
}) {
  const { configure } = useMcpActions();
  const rows = server.tools.filter((tool) => tool.group === group.value);
  if (rows.length === 0) return null;
  const save = (tool: McpTool, settings: { policy?: ToolPolicy; group?: ToolGroup }) =>
    void configure.run({ server, tool: tool.name, settings });
  return (
    <section className="kw-detail-section">
      <h3>{group.title}</h3>
      <Table<McpTool>
        rowKey={(tool) => tool.name}
        pageSize={false}
        rows={rows}
        columns={[
          { key: 'name', title: '工具', render: (tool) => <ToolName tool={tool} /> },
          {
            key: 'policy',
            title: '调用',
            width: 230,
            render: (tool) => (
              <SegmentedControl<ToolPolicy>
                aria-label={`${tool.rawName} 怎么调用`}
                size="sm"
                value={tool.policy}
                onChange={(policy) => save(tool, { policy })}
              >
                {POLICIES.map((p) => (
                  <SegmentedControl.Option key={p.value} value={p.value}>
                    {p.label}
                  </SegmentedControl.Option>
                ))}
              </SegmentedControl>
            ),
          },
          {
            key: 'group',
            title: '',
            width: 110,
            render: (tool) => (
              <Button
                color="secondary"
                variant="ghost"
                size="sm"
                onClick={() => save(tool, { group: group.value === 'read' ? 'write' : 'read' })}
              >
                {group.other}
              </Button>
            ),
          },
        ]}
      />
    </section>
  );
}

function authText(server: McpServer) {
  if (server.auth === 'shared')
    return server.headers.length
      ? '共享凭据：所有人共用管理员配的这份凭据（同一个账号的权限）'
      : '无需认证：对方不要凭据，所有人直接用';
  if (server.auth === 'oauth') {
    const app = server.oauth?.dynamic ? '自动注册的应用' : `应用 ${server.oauth?.clientId ?? ''}`;
    return `OAuth 授权（${app}）：成员在「设置 → 连接」里授权自己的账号后才能用，按本人的权限调用`;
  }
  return '个人访问令牌：成员在「设置 → 连接」里填自己的令牌后才能用，按本人的权限调用';
}

/** OAuth 授权的服务：管理员授权自己的账号（刚登记还没有工具清单时，授权回来就拉）。 */
function LoginNote({ server }: { readonly server: McpServer }) {
  const [going, login] = useMcpLogin();
  const { returnTo } = useMcpScope();
  const waiting = !server.syncedAt;
  return (
    <p className="kw-detail-note">
      {waiting
        ? '还没拉到工具清单：登记人授权一次自己的账号，回来时自动拉取。'
        : '刷新工具清单用的是你自己的授权；没授权或已失效时先授权。'}
      <Button
        color="secondary"
        variant="ghost"
        size="sm"
        loading={going === server.id}
        onClick={() => void login(server.id, returnTo)}
      >
        {waiting ? '授权并拉取工具清单' : '授权我的账号'}
      </Button>
    </p>
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
  const { reach } = useMcpScope();
  if (serverId === undefined) return null;
  const server = servers.data?.find((s) => s.id === serverId);
  const status = server ? serverStatus(server) : null;
  const fresh = server ? newCount(server) : 0;
  return (
    <Sheet title={server?.title ?? 'MCP 服务'} width={1000} onClose={onClose}>
      {server && status ? (
        <>
          <DescriptionList
            items={[
              { label: '地址', value: server.url },
              { label: '工具名前缀', value: <code className="kw-code">{`mcp__${server.name}__`}</code> },
              { label: '状态', value: <StatusDot tone={status.status}>{status.text}</StatusDot> },
              { label: '认证方式', value: authText(server) },
              { label: reach.title, value: <reach.Row server={server} /> },
              {
                label: '可用工具',
                value: <span className="kw-num">{`${usableCount(server)} / ${server.tools.length}`}</span>,
              },
              { label: '最近刷新', value: formatDateTime(server.syncedAt) },
            ]}
          />
          {server.promotedTo ? (
            <p className="kw-detail-note">
              这个服务已收为企业 MCP「{server.promotedTo}
              」。确认企业 MCP 在本空间能用后，可以删掉这份；两份都在时，成员会看到两套同样的工具。
            </p>
          ) : null}
          {server.auth === 'oauth' ? <LoginNote server={server} /> : null}
          {server.syncError ? (
            <p className="kw-detail-note">
              最近一次刷新失败：{server.syncError}。对话里仍按上次拉到的工具清单使用。
            </p>
          ) : null}
          <p className="kw-detail-note">
            允许：模型直接调用；需确认：调用前请用户批准；禁用：不给模型。
            {fresh > 0 ? `有 ${fresh} 个新出现的工具，已先设成需确认。` : ''}
          </p>
          {GROUPS.map((group) => (
            <ToolGroupTable key={group.value} server={server} group={group} />
          ))}
        </>
      ) : null}
    </Sheet>
  );
}
