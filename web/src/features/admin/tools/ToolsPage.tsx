/**
 * 工具与 MCP：登记 MCP 服务，Agent 就能调用它提供的工具；新登记的服务默认全体成员可用，在详情里改「给谁用」；
 * 可刷新工具清单、逐个工具设置（允许、需确认、禁用）、停用、删除。
 * 装了扩展时分「空间 MCP / 企业」两个页签（企业见 HereMcp.tsx）；列表（ServerList）按范围（scope.ts）取接口，
 * 扩展给系统管理员的企业 MCP 管理视图也用它。
 */
import { useMemo, useState, type ReactNode } from 'react';
import { useSearchParams } from 'react-router';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { Plus } from '@openai/apps-sdk-ui/components/Icon';
import type { McpServer } from '../../../api/mcp';
import { formatDateTime } from '../../../utils/format';
import { slotItems } from '../../../slots/registry';
import { Tabs } from '../../../ui/Tabs';
import { AdminPage, Toolbar } from '../AdminPage';
import { RowActions } from '../common';
import { SearchInput } from '../../../ui/SearchInput';
import { Skeleton } from '../../../ui/Skeleton';
import { StatusDot } from '../../../ui/StatusDot';
import { Table, type Column } from '../../../ui/Table';
import { useMcpActions, useMcpServers } from './queries';
import { ServerDetail } from './ServerDetail';
import { ServerDialog } from './ServerDialog';
import { useMcpLoginResult } from './login';
import { useGrantsList } from '../grants/users';
import { EnterpriseMcp } from './HereMcp';
import { useMcpScope, type ExtraColumn } from './scope';
import { filterServers, newCount, serverStatus, usableCount } from './toolsModel';

interface Handlers {
  readonly detail: (server: McpServer) => void;
  readonly edit: (server: McpServer) => void;
}

function useColumns(
  { detail, edit }: Handlers,
  extra: ExtraColumn | undefined,
): readonly Column<McpServer>[] {
  const actions = useMcpActions();
  const { reach, deleteConfirm } = useMcpScope();
  useGrantsList(); // 「给谁用」各格要的授权列表和服务列表一起取，不等列表出来再取
  return [
    {
      key: 'title',
      title: '名称',
      width: 200,
      ellipsis: true,
      render: (s) => (
        <span className="kw-labeled">
          <span>{s.title}</span>
          <span className="kw-secondary-text">{s.promotedTo ? '已收为企业 MCP' : s.name}</span>
        </span>
      ),
    },
    { key: 'url', title: '地址', ellipsis: true, render: (s) => s.url },
    {
      key: 'tools',
      title: '可用工具',
      width: 120,
      align: 'right',
      render: (s) => (
        <span className="kw-labeled">
          <span className="kw-num">{`${usableCount(s)} / ${s.tools.length}`}</span>
          {newCount(s) > 0 ? <span className="kw-secondary-text">{`新 ${newCount(s)}`}</span> : null}
        </span>
      ),
    },
    {
      key: 'users',
      title: reach.title,
      width: 140,
      ellipsis: true,
      render: (s) => <reach.Cell server={s} />,
    },
    ...(extra
      ? [
          {
            key: 'extra',
            title: extra.title,
            width: 130,
            ellipsis: true,
            render: (s: McpServer) => extra.cell(s),
          },
        ]
      : []),
    {
      key: 'status',
      title: '状态',
      width: 110,
      render: (s) => {
        const status = serverStatus(s);
        return <StatusDot tone={status.status}>{status.text}</StatusDot>;
      },
    },
    {
      key: 'synced',
      title: '最近刷新',
      width: 170,
      render: (s) => <span className="kw-num">{formatDateTime(s.syncedAt)}</span>,
    },
    {
      key: 'actions',
      title: '操作',
      width: 190,
      render: (s) => (
        <RowActions
          actions={[
            ...(extra?.actionsOf(s).slice(0, 1) ?? []),
            { key: 'detail', label: '工具', onClick: () => detail(s) },
            {
              key: 'sync',
              label: '刷新',
              disabled: actions.sync.pending,
              onClick: () => void actions.sync.run(s),
            },
            { key: 'edit', label: '修改', onClick: () => edit(s) },
            {
              key: 'toggle',
              label: s.enabled ? '停用' : '启用',
              onClick: () => void actions.toggle.run({ server: s, enabled: !s.enabled }),
            },
            {
              key: 'delete',
              label: '删除',
              danger: true,
              confirm: deleteConfirm(s),
              onClick: () => void actions.remove.run(s),
            },
            ...(extra?.actionsOf(s).slice(1) ?? []),
          ]}
        />
      ),
    },
  ];
}

/** MCP 服务列表：工具栏、表格、登记对话框与详情抽屉，按当前范围（空间服务或企业 MCP）。 */
export function ServerList({
  addLabel = '添加 MCP 服务',
  toolbarExtra,
}: {
  readonly addLabel?: string;
  /** 工具栏右侧、添加按钮前面多放的东西（例如扩展的「从空间 MCP 收为企业 MCP」） */
  readonly toolbarExtra?: ReactNode;
}) {
  const scope = useMcpScope();
  useMcpLoginResult([scope.key]); // 登录回来时服务端可能刚拉完工具清单
  const servers = useMcpServers();
  const extra = scope.useExtra();
  const [keyword, setKeyword] = useState('');
  const [editing, setEditing] = useState<{ readonly item?: McpServer } | null>(null);
  const [detailId, setDetailId] = useState<string | undefined>();
  const columns = useColumns(
    { detail: (s) => setDetailId(s.id), edit: (s) => setEditing({ item: s }) },
    extra,
  );
  const rows = useMemo(() => filterServers(servers.data ?? [], keyword), [servers.data, keyword]);

  return (
    <>
      <Toolbar
        left={<SearchInput placeholder="搜索名称、地址或工具" value={keyword} onChange={setKeyword} />}
        right={
          <>
            {toolbarExtra}
            <Button color="primary" onClick={() => setEditing({})}>
              <Plus />
              {addLabel}
            </Button>
          </>
        }
      />
      {servers.isPending ? (
        <Skeleton rows={5} />
      ) : (
        <Table<McpServer>
          rowKey={(s) => s.id}
          columns={columns}
          rows={rows}
          minWidth={1080}
          loading={servers.isFetching}
          empty={
            servers.isError
              ? 'MCP 服务加载失败，请刷新重试'
              : keyword
                ? '没有找到匹配的 MCP 服务'
                : '还没有 MCP 服务。添加一个 Streamable HTTP 地址开始'
          }
        />
      )}
      {editing ? <ServerDialog server={editing.item} onClose={() => setEditing(null)} /> : null}
      <ServerDetail serverId={detailId} onClose={() => setDetailId(undefined)} />
      {extra?.dialog}
    </>
  );
}

export default function ToolsPage() {
  const [params, setParams] = useSearchParams();
  // 企业 MCP 是扩展的能力：注册了才有「企业」页签，开源版页面照旧
  const enterprise = slotItems('admin.tools.enterprise').length > 0;
  const items = [
    { key: 'all', label: '空间 MCP', children: <ServerList /> },
    { key: 'enterprise', label: '企业', children: <EnterpriseMcp /> },
  ];
  const value = items.find((item) => item.key === params.get('tab'))?.key ?? 'all';
  return (
    <AdminPage
      title="工具与 MCP"
      description="登记 MCP 服务，Agent 就能调用它提供的工具。新登记的服务默认全体成员可用；每个工具可以设成允许、需确认或禁用。"
    >
      {enterprise ? (
        <Tabs items={items} value={value} onChange={(tab) => setParams({ tab }, { replace: true })} />
      ) : (
        <ServerList />
      )}
    </AdminPage>
  );
}
