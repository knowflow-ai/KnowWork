/** 个人设置的各个标签页：账号、API Key、我的用量。 */
import { useState } from 'react';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { Plus } from '@openai/apps-sdk-ui/components/Icon';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { API_KEY_SCOPES, listApiKeys, revokeApiKey, type ApiKey } from '../../api/identity';
import { fetchMyUsage } from '../../api/models';
import { formatDateTime } from '../../utils/format';
import { useMe } from '../auth/queries';
import { Toolbar } from '../admin/AdminPage';
import { RowActions } from '../admin/common';
import { useAction } from '../admin/useAction';
import { DescriptionList } from '../../ui/DescriptionList';
import { Skeleton } from '../../ui/Skeleton';
import { Table, type Column } from '../../ui/Table';
import { UsageView } from '../usage/UsageView';
import { CreateApiKeyDialog } from './ApiKeyDialogs';

const API_KEYS = ['me', 'api-keys'] as const;
const scopeLabel = (scope: string) => API_KEY_SCOPES.find((s) => s.value === scope)?.label ?? scope;

export function AccountTab() {
  const me = useMe();
  return (
    <DescriptionList
      className="kw-settings-account"
      items={[
        { label: '姓名', value: me.data?.name ?? '—' },
        { label: '邮箱', value: me.data?.email ?? '—' },
        { label: '工作空间', value: '默认空间' },
      ]}
    />
  );
}

/** 使用端的状态只写文字不上色（界面规范 2.11）。 */
function keyStatus(key: ApiKey, now = Date.now()) {
  if (key.revoked) return <span className="kw-secondary-text">已吊销</span>;
  if (key.expiresAt && new Date(key.expiresAt).getTime() < now)
    return <span className="kw-secondary-text">已过期</span>;
  return '有效';
}

export function ApiKeysTab() {
  const client = useQueryClient();
  const keys = useQuery({ queryKey: API_KEYS, queryFn: listApiKeys });
  const [creating, setCreating] = useState(false);
  const revoke = useAction((id: string) => revokeApiKey(id), {
    invalidate: [API_KEYS],
    success: '已吊销',
    failure: '吊销没有成功，请稍后重试',
  });
  const columns: readonly Column<ApiKey>[] = [
    { key: 'name', title: '名称', width: 150, ellipsis: true, render: (k) => k.name },
    {
      key: 'prefix',
      title: '前缀',
      width: 130,
      render: (k) => <span className="kw-num">{k.prefix ? `${k.prefix}…` : '—'}</span>,
    },
    { key: 'scopes', title: '权限范围', ellipsis: true, render: (k) => k.scopes.map(scopeLabel).join('、') },
    { key: 'lastUsed', title: '最近使用', width: 150, render: (k) => formatDateTime(k.lastUsedAt) },
    {
      key: 'expires',
      title: '过期时间',
      width: 150,
      render: (k) => (k.expiresAt ? formatDateTime(k.expiresAt) : '永不过期'),
    },
    { key: 'status', title: '状态', width: 90, render: (k) => keyStatus(k) },
    {
      key: 'actions',
      title: '操作',
      width: 90,
      render: (k) =>
        k.revoked ? null : (
          <RowActions
            actions={[
              {
                key: 'revoke',
                label: '吊销',
                danger: true,
                confirm: `吊销「${k.name}」？用它的脚本会立即失效。`,
                onClick: () => void revoke.run(k.id),
              },
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
            给脚本或其他系统调用 KnowWork 接口用。Key 的身份就是你本人。各接口的参数见
            <a className="kw-link" href="/api/docs" target="_blank" rel="noreferrer">
              接口文档
            </a>
            。
          </span>
        }
        right={
          <Button color="primary" onClick={() => setCreating(true)}>
            <Plus />
            创建 API Key
          </Button>
        }
      />
      {keys.isPending ? (
        <Skeleton rows={4} />
      ) : (
        <Table<ApiKey>
          rowKey={(k) => k.id}
          columns={columns}
          rows={keys.data ?? []}
          minWidth={900}
          empty={keys.isError ? 'API Key 加载失败，请刷新重试' : '还没有 API Key'}
        />
      )}
      {creating ? (
        <CreateApiKeyDialog
          onCreated={() => void client.invalidateQueries({ queryKey: API_KEYS })}
          onClose={() => setCreating(false)}
        />
      ) : null}
    </>
  );
}

const MY_GROUPINGS = [
  { value: 'model', label: '按模型' },
  { value: 'day', label: '按天' },
] as const;

export function MyUsageTab() {
  return <UsageView queryKey={['me', 'usage']} fetcher={fetchMyUsage} groupings={MY_GROUPINGS} />;
}
