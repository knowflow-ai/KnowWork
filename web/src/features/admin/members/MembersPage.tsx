/** 成员：列表、加人、改角色、移出、邀请新用户；接了组织目录时多一个「成员规则」（按组织架构里的部门自动加人）。 */
import { useMemo, useState } from 'react';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { Plus } from '@openai/apps-sdk-ui/components/Icon';
import { peopleOnly, removeMember, roleLabel, type Member } from '../../../api/workspace';
import { useMe } from '../../auth/queries';
import { AdminPage, Toolbar } from '../AdminPage';
import { RowActions } from '../common';
import { useAction } from '../useAction';
import { includesText } from '../table';
import { SearchInput } from '../../../ui/SearchInput';
import { Skeleton } from '../../../ui/Skeleton';
import { Table, type Column } from '../../../ui/Table';
import { Tabs } from '../../../ui/Tabs';
import { KEYS, useMemberRules, useMembers, useOrgDepartments } from '../queries';
import { AddMemberDialog, InviteDialog, RoleDialog } from './MemberDialogs';
import { MemberRulesTab } from './MemberRulesTab';

type Dialog =
  { readonly type: 'add' } | { readonly type: 'invite' } | { readonly type: 'role'; readonly member: Member };

function MemberList() {
  const members = useMembers();
  const me = useMe();
  const [keyword, setKeyword] = useState('');
  const [dialog, setDialog] = useState<Dialog | null>(null);
  const remove = useAction((userId: string) => removeMember(userId), {
    invalidate: [KEYS.members],
    success: '已移出',
    failure: '移出没有成功，请稍后重试',
  });
  const rows = useMemo(
    () =>
      peopleOnly(members.data ?? []).filter((m) =>
        includesText([m.name, m.email, m.userId, roleLabel(m.role)], keyword),
      ),
    [members.data, keyword],
  );

  const columns: readonly Column<Member>[] = [
    {
      key: 'member',
      title: '成员',
      ellipsis: true,
      render: (m) => (
        <span className="kw-cell-stack">
          <span>{m.name ?? m.userId}</span>
          {/* 企业微信等目录来的人多半没有邮箱，不写「没有取到邮箱」 */}
          {m.email ? <span className="kw-secondary">{m.email}</span> : null}
        </span>
      ),
    },
    {
      key: 'role',
      title: '角色',
      width: 140,
      render: (m) => (
        <span className="kw-cell-stack">
          <span>{roleLabel(m.role)}</span>
          {m.origin === 'rule' ? <span className="kw-secondary">按成员规则加入</span> : null}
        </span>
      ),
    },
    {
      key: 'actions',
      title: '操作',
      width: 160,
      render: (m) => (
        <RowActions
          actions={[
            { key: 'role', label: '改角色', onClick: () => setDialog({ type: 'role', member: m }) },
            {
              key: 'remove',
              label: '移出',
              danger: true,
              // 按规则进来的人随部门进出，要移出就改成员规则
              disabled: m.userId === me.data?.id || m.origin === 'rule',
              confirm: `把「${m.name ?? m.userId}」移出空间？他在本空间的部门、用户组与授权也会一并撤销。`,
              onClick: () => void remove.run(m.userId),
            },
          ]}
        />
      ),
    },
  ];

  return (
    <>
      <Toolbar
        left={<SearchInput placeholder="搜索姓名、邮箱或角色" value={keyword} onChange={setKeyword} />}
        right={
          <>
            <Button color="secondary" variant="outline" onClick={() => setDialog({ type: 'invite' })}>
              邀请新用户
            </Button>
            <Button color="primary" onClick={() => setDialog({ type: 'add' })}>
              <Plus />
              添加成员
            </Button>
          </>
        }
      />
      {members.isPending ? (
        <Skeleton rows={6} />
      ) : (
        <Table<Member>
          rowKey={(m) => m.userId}
          columns={columns}
          rows={rows}
          loading={members.isFetching}
          empty={
            members.isError ? '成员列表加载失败，请刷新重试' : keyword ? '没有找到匹配的成员' : '还没有成员'
          }
        />
      )}
      {dialog?.type === 'add' ? (
        <AddMemberDialog members={members.data ?? []} onClose={() => setDialog(null)} />
      ) : null}
      {dialog?.type === 'invite' ? <InviteDialog onClose={() => setDialog(null)} /> : null}
      {dialog?.type === 'role' ? <RoleDialog member={dialog.member} onClose={() => setDialog(null)} /> : null}
    </>
  );
}

export default function MembersPage() {
  const departments = useOrgDepartments();
  const rules = useMemberRules();
  // 接了组织目录（有组织架构），或者以前建过规则，才有「成员规则」；没接目录时页面和原来一样。
  // 先等这两样取回来再定版式：先画列表、再换成带页签的会闪一下，列表也会重新挂载（搜索框清空）
  const deciding = departments.isPending || rules.isPending;
  const withRules = (departments.data ?? []).length > 0 || (rules.data ?? []).length > 0;
  return (
    <AdminPage
      title="成员"
      description="管理工作空间里有哪些人、各自是什么角色。所有者与管理员能进入管理页面。"
    >
      {deciding ? (
        <Skeleton rows={6} />
      ) : withRules ? (
        <Tabs
          items={[
            { key: 'members', label: '成员', children: <MemberList /> },
            { key: 'rules', label: '成员规则', children: <MemberRulesTab /> },
          ]}
        />
      ) : (
        <MemberList />
      )}
    </AdminPage>
  );
}
