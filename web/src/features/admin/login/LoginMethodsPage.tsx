/**
 * 登录方式：账号密码之外，后台配置的单点登录（OIDC）与通用 OAuth2。全平台共用，只有系统管理员能改；
 * 环境变量配的 OIDC 也列在这里，只能看，要改请改环境变量。
 */
import { useState } from 'react';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { Lock, Plus } from '@openai/apps-sdk-ui/components/Icon';
import {
  deleteLoginProvider,
  KIND_LABEL,
  SIGNUP_LABEL,
  type LoginProvider,
} from '../../../api/loginProviders';
import { Empty } from '../../../ui/Empty';
import { Skeleton } from '../../../ui/Skeleton';
import { StatusDot } from '../../../ui/StatusDot';
import { Table, type Column } from '../../../ui/Table';
import { useIsSystemAdmin } from '../../brand/queries';
import { AdminPage, Toolbar } from '../AdminPage';
import { RowActions } from '../common';
import { useAction } from '../useAction';
import { LoginProviderDialog } from './LoginProviderDialog';
import { LOGIN_PROVIDERS_KEY, useLoginProviders } from './queries';

type Editing = { readonly item?: LoginProvider } | null;

function ProvidersTable() {
  const providers = useLoginProviders();
  const [editing, setEditing] = useState<Editing>(null);
  const remove = useAction((id: string) => deleteLoginProvider(id), {
    invalidate: [LOGIN_PROVIDERS_KEY],
    success: '已删除登录方式',
    failure: '删除没有成功，请稍后重试',
  });
  const columns: readonly Column<LoginProvider>[] = [
    { key: 'name', title: '名称', ellipsis: true, render: (p) => p.name },
    {
      key: 'kind',
      title: '类型',
      width: 150,
      render: (p) => (p.readonly ? `${KIND_LABEL[p.kind]} · 环境变量` : KIND_LABEL[p.kind]),
    },
    { key: 'signup', title: '谁能登录', width: 150, render: (p) => SIGNUP_LABEL[p.signup] },
    {
      key: 'enabled',
      title: '状态',
      width: 90,
      render: (p) => (
        <StatusDot tone={p.enabled ? 'success' : 'default'}>{p.enabled ? '启用' : '停用'}</StatusDot>
      ),
    },
    {
      key: 'actions',
      title: '操作',
      width: 130,
      render: (p) =>
        p.readonly ? (
          <span className="kw-secondary">改环境变量</span>
        ) : (
          <RowActions
            actions={[
              { key: 'edit', label: '修改', onClick: () => setEditing({ item: p }) },
              {
                key: 'delete',
                label: '删除',
                danger: true,
                confirm: `删除登录方式「${p.name}」？用它登录过的人账号照旧保留，之后改用别的方式登录。`,
                onClick: () => void remove.run(p.id),
              },
            ]}
          />
        ),
    },
  ];
  return (
    <>
      <Toolbar
        right={
          <Button color="primary" onClick={() => setEditing({})}>
            <Plus />
            添加登录方式
          </Button>
        }
      />
      {providers.isPending ? (
        <Skeleton rows={4} />
      ) : (
        <Table<LoginProvider>
          rowKey={(p) => p.id}
          columns={columns}
          rows={providers.data ?? []}
          loading={providers.isFetching}
          empty={
            providers.isError ? '登录方式加载失败，请刷新重试' : '还没有登录方式，员工只能用账号密码登录'
          }
        />
      )}
      {editing ? (
        <LoginProviderDialog
          // 新建保存后换成修改这一条（显示回调地址），按 id 重新挂载表单
          key={editing.item?.id ?? 'new'}
          provider={editing.item}
          onClose={() => setEditing(null)}
          onCreated={(created) => setEditing({ item: created })}
        />
      ) : null}
    </>
  );
}

export default function LoginMethodsPage() {
  const systemAdmin = useIsSystemAdmin();
  if (systemAdmin.isPending) return <Skeleton rows={6} className="kw-page-state" />;
  if (!systemAdmin.data) {
    return (
      <Empty
        area
        icon={<Lock />}
        title="没有权限"
        description="登录方式是所有空间共用的，只有系统管理员（初始化时创建的账号）能配置"
      />
    );
  }
  return (
    <AdminPage
      title="登录方式"
      description="账号密码之外，员工还可以用企业的身份中台（OIDC）或统一认证（OAuth2）登录。保存后把回调地址登记到对方，登录页就会出现对应的按钮。"
    >
      <ProvidersTable />
    </AdminPage>
  );
}
