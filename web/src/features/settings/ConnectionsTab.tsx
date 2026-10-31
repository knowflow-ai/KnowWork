/**
 * 个人设置的「连接」：列出本人在对话里能用的全部外部服务（MCP）与工具，让成员知道能让 AI 做什么。
 * 无需认证、共享凭据的服务可以直接用；要用自己凭据的在这里连接自己的账号：
 * - OAuth 授权：点「连接」跳到对方页面授权，回来时这一页提示结果；
 * - 个人访问令牌：填自己的令牌，保存前服务端会拿它试连一次，不对就不保存。令牌只能写不能读，列表里只显示末四位。
 * 连上后对话里用这些服务时按本人的权限办事；对方拒绝了凭据（被吊销、过期）会标成失效，要重新连接。
 */
import { useState } from 'react';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { Input } from '@openai/apps-sdk-ui/components/Input';
import { useQuery } from '@tanstack/react-query';
import { connectMcp, disconnectMcp, listMyConnections, type MyConnection } from '../../api/mcp';
import { Toolbar } from '../admin/AdminPage';
import { RowActions } from '../admin/common';
import { useMcpLogin, useMcpLoginResult } from '../admin/tools/login';
import { useAction } from '../admin/useAction';
import { Dialog, DialogActions } from '../../ui/Dialog';
import { Field } from '../../ui/Field';
import { Skeleton } from '../../ui/Skeleton';
import { Table, type Column } from '../../ui/Table';
import { toast } from '../../ui/toastStore';

const CONNECTIONS = ['me', 'mcp-connections'] as const;
const LOGIN_REFRESH = [CONNECTIONS];
const HERE = '/settings?tab=connections';

/** 已连接时写明用的什么凭据：OAuth 授权，或个人访问令牌的末四位。 */
function credential(connection: MyConnection) {
  if (connection.auth === 'oauth') return 'OAuth 授权';
  return connection.display ? `令牌 ${connection.display}` : '个人访问令牌';
}

/** 使用端的状态只写文字不上色（界面规范 2.11）。 */
function statusText(connection: MyConnection) {
  if (connection.status === 'ready') return '可以直接用';
  if (connection.status === 'connected') return `已连接 · ${credential(connection)}`;
  if (connection.status === 'invalid') return <span className="kw-secondary-text">已失效，请重新连接</span>;
  return <span className="kw-secondary-text">未连接</span>;
}

function ToolsCell({
  connection,
  onOpen,
}: {
  readonly connection: MyConnection;
  readonly onOpen: () => void;
}) {
  const names = connection.tools.map((tool) => tool.name).join('、');
  return (
    <span className="kw-labeled">
      <span className="kw-ellipsis" title={names}>
        {`${names}（${connection.tools.length} 个）`}
      </span>
      <Button color="secondary" variant="ghost" size="sm" onClick={onOpen}>
        查看
      </Button>
    </span>
  );
}

function ToolsDialog({
  connection,
  onClose,
}: {
  readonly connection: MyConnection;
  readonly onClose: () => void;
}) {
  return (
    <Dialog
      title={`「${connection.title}」的工具`}
      description="对话里 AI 会按需调用这些工具；写入类的调用前会先问你。"
      onClose={onClose}
      footer={<DialogActions okText="知道了" onOk={onClose} />}
    >
      <div className="kw-tool-list">
        {connection.tools.map((tool) => (
          <div key={tool.name} className="kw-tool-name">
            <strong>{tool.name}</strong>
            {tool.description ? <span className="kw-secondary-text">{tool.description}</span> : null}
          </div>
        ))}
      </div>
    </Dialog>
  );
}

function ConnectDialog({
  connection,
  onClose,
}: {
  readonly connection: MyConnection;
  readonly onClose: () => void;
}) {
  const [token, setToken] = useState('');
  const connect = useAction((value: string) => connectMcp(connection.serverId, value), {
    invalidate: [CONNECTIONS],
    success: `已连接，新开一个对话就能用「${connection.title}」`,
    failure: '连接没有成功',
  });
  const submit = async () => {
    if (!token.trim()) {
      toast.warning('请填写令牌');
      return;
    }
    if (await connect.run(token)) onClose();
  };
  return (
    <Dialog
      title={`连接「${connection.title}」`}
      onClose={onClose}
      onSubmit={() => void submit()}
      footer={
        <DialogActions
          okText={connect.pending ? '正在验证' : '连接'}
          loading={connect.pending}
          onCancel={onClose}
        />
      }
    >
      <Field
        label="你的令牌"
        help={connection.help || '在这个服务里生成一个个人访问令牌，粘贴到这里。保存前会先试连一次'}
      >
        {(id) => (
          <Input
            id={id}
            type="password"
            autoComplete="new-password"
            value={token}
            onChange={(e) => setToken(e.target.value)}
          />
        )}
      </Field>
    </Dialog>
  );
}

export function ConnectionsTab() {
  useMcpLoginResult(LOGIN_REFRESH);
  const connections = useQuery({ queryKey: CONNECTIONS, queryFn: listMyConnections });
  const [connecting, setConnecting] = useState<MyConnection>();
  const [viewing, setViewing] = useState<MyConnection>();
  const [going, login] = useMcpLogin();
  const connect = (c: MyConnection) => (c.auth === 'oauth' ? void login(c.serverId, HERE) : setConnecting(c));
  const disconnect = useAction((serverId: string) => disconnectMcp(serverId), {
    invalidate: [CONNECTIONS],
    success: '已断开',
    failure: '断开没有成功，请稍后重试',
  });
  const columns: readonly Column<MyConnection>[] = [
    { key: 'title', title: '服务', width: 180, ellipsis: true, render: (c) => c.title },
    {
      key: 'tools',
      title: '工具',
      render: (c) => <ToolsCell connection={c} onOpen={() => setViewing(c)} />,
    },
    { key: 'status', title: '状态', width: 200, render: statusText },
    {
      key: 'actions',
      title: '操作',
      width: 140,
      render: (c) =>
        c.status === 'ready' ? null : (
          <RowActions
            actions={[
              {
                key: 'connect',
                label: c.status === 'none' ? '连接' : '重新连接',
                disabled: going !== undefined,
                onClick: () => connect(c),
              },
              ...(c.status === 'none'
                ? []
                : [
                    {
                      key: 'disconnect',
                      label: '断开',
                      danger: true,
                      confirm: `断开「${c.title}」？对话里就不能再用它了。`,
                      onClick: () => void disconnect.run(c.serverId),
                    },
                  ]),
            ]}
          />
        ),
    },
  ];
  return (
    <>
      <Toolbar
        left={
          <span className="kw-secondary">
            这是你在对话里能用的外部服务和它们的工具，AI 会按需调用。标着「未连接」的要先用你自己的账号连接，
            连上后按你本人的权限使用；凭据加密保存，只有你的对话会用到。
          </span>
        }
      />
      {connections.isPending ? (
        <Skeleton rows={3} />
      ) : (
        <Table<MyConnection>
          rowKey={(c) => c.serverId}
          columns={columns}
          rows={connections.data ?? []}
          minWidth={760}
          empty={connections.isError ? '连接加载失败，请刷新重试' : '还没有你能用的外部服务'}
        />
      )}
      {connecting ? <ConnectDialog connection={connecting} onClose={() => setConnecting(undefined)} /> : null}
      {viewing ? <ToolsDialog connection={viewing} onClose={() => setViewing(undefined)} /> : null}
    </>
  );
}
