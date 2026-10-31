/**
 * 「工具与 MCP」的「企业」页签：到达本空间的企业 MCP。空间管理员在这里停用、改给谁用；系统管理员看扩展经插槽
 * admin.tools.enterprise 给的管理视图（登记、发给哪些空间……连同本空间的设置）。开源版没有企业 MCP，不显示这个页签。
 */
import type { EnterpriseHere } from '../../../api/mcp';
import { readableError } from '../../../api/http';
import { slotItems } from '../../../slots/registry';
import { Skeleton } from '../../../ui/Skeleton';
import { Table, type Column } from '../../../ui/Table';
import { useIsSystemAdmin } from '../../brand/queries';
import { RowActions } from '../common';
import { useGrantsList } from '../grants/users';
import { HERE_NOTE, hereText, useHereActions } from './here';
import { useEnterpriseHere } from './queries';

const AUTH_TEXT = { shared: '不用连接', user_token: '个人访问令牌', oauth: 'OAuth 授权' } as const;

export function HereMcp() {
  const servers = useEnterpriseHere();
  const grants = useGrantsList();
  const { actionsOf, dialog } = useHereActions();
  const columns: readonly Column<EnterpriseHere>[] = [
    { key: 'title', title: '名称', width: 200, ellipsis: true, render: (s) => s.title },
    { key: 'url', title: '地址', ellipsis: true, render: (s) => s.url },
    { key: 'auth', title: '成员要做的', width: 130, render: (s) => AUTH_TEXT[s.auth] },
    {
      key: 'tools',
      title: '可用工具',
      width: 90,
      align: 'right',
      render: (s) => <span className="kw-num">{s.tools}</span>,
    },
    { key: 'users', title: '给谁用', width: 130, render: (s) => hereText(s, grants.data) },
    { key: 'actions', title: '操作', width: 200, render: (s) => <RowActions actions={actionsOf(s)} /> },
  ];
  if (servers.isPending) return <Skeleton rows={4} />;
  return (
    <>
      <p className="kw-detail-note">{HERE_NOTE}</p>
      <Table<EnterpriseHere>
        rowKey={(s) => s.serverId}
        columns={columns}
        rows={servers.data ?? []}
        minWidth={900}
        loading={servers.isFetching}
        empty={
          servers.isError
            ? readableError(servers.error, '企业 MCP 没有加载出来')
            : '还没有发到本空间的企业 MCP'
        }
      />
      {dialog}
    </>
  );
}

/** 「企业」页签：系统管理员看扩展给的管理视图，其他管理员看本空间的设置。 */
export function EnterpriseMcp() {
  const isSystemAdmin = useIsSystemAdmin().data === true;
  const [panel] = slotItems('admin.tools.enterprise');
  return isSystemAdmin && panel ? <>{panel.children}</> : <HereMcp />;
}
