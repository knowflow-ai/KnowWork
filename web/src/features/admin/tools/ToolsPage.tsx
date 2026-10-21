/** 工具与 MCP：登记 MCP 服务，Agent 就能调用它提供的工具；可刷新工具清单、逐个工具设置、停用、删除。 */
import { useMemo, useState } from 'react';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { Plus } from '@openai/apps-sdk-ui/components/Icon';
import type { McpServer } from '../../../api/mcp';
import { formatDateTime } from '../../../utils/format';
import { AdminPage, Toolbar } from '../AdminPage';
import { RowActions } from '../common';
import { SearchInput } from '../../../ui/SearchInput';
import { Skeleton } from '../../../ui/Skeleton';
import { StatusDot } from '../../../ui/StatusDot';
import { Table, type Column } from '../../../ui/Table';
import { useMcpActions, useMcpServers } from './queries';
import { ServerDetail } from './ServerDetail';
import { ServerDialog } from './ServerDialog';
import { filterServers, serverStatus, usableCount } from './toolsModel';

interface Handlers {
  readonly detail: (server: McpServer) => void;
  readonly edit: (server: McpServer) => void;
}

function useColumns({ detail, edit }: Handlers): readonly Column<McpServer>[] {
  const actions = useMcpActions();
  return [
    {
      key: 'title',
      title: '名称',
      width: 200,
      ellipsis: true,
      render: (s) => (
        <span className="kw-labeled">
          <span>{s.title}</span>
          <span className="kw-secondary-text">{s.name}</span>
        </span>
      ),
    },
    { key: 'url', title: '地址', ellipsis: true, render: (s) => s.url },
    {
      key: 'tools',
      title: '可用工具',
      width: 100,
      align: 'right',
      render: (s) => <span className="kw-num">{`${usableCount(s)} / ${s.tools.length}`}</span>,
    },
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
              confirm: `删除 MCP 服务「${s.title}」？对话里将不能再调用它的工具，相关授权需要另行清理。`,
              onClick: () => void actions.remove.run(s),
            },
          ]}
        />
      ),
    },
  ];
}

export default function ToolsPage() {
  const servers = useMcpServers();
  const [keyword, setKeyword] = useState('');
  const [editing, setEditing] = useState<{ readonly item?: McpServer } | null>(null);
  const [detailId, setDetailId] = useState<string | undefined>();
  const columns = useColumns({ detail: (s) => setDetailId(s.id), edit: (s) => setEditing({ item: s }) });
  const rows = useMemo(() => filterServers(servers.data ?? [], keyword), [servers.data, keyword]);

  return (
    <AdminPage
      title="工具与 MCP"
      description="登记 MCP 服务，Agent 就能调用它提供的工具。写入与高风险的工具执行前要用户批准。"
    >
      <Toolbar
        left={<SearchInput placeholder="搜索名称、地址或工具" value={keyword} onChange={setKeyword} />}
        right={
          <Button color="primary" onClick={() => setEditing({})}>
            <Plus />
            添加 MCP 服务
          </Button>
        }
      />
      {servers.isPending ? (
        <Skeleton rows={5} />
      ) : (
        <Table<McpServer>
          rowKey={(s) => s.id}
          columns={columns}
          rows={rows}
          minWidth={960}
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
    </AdminPage>
  );
}
