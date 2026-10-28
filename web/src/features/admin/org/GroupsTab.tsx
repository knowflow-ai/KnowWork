/** 用户组：列表、新建、重命名、删除；成员在抽屉里加入与移出。 */
import { useState } from 'react';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { Plus } from '@openai/apps-sdk-ui/components/Icon';
import {
  addGroupMember,
  createGroup,
  deleteGroup,
  peopleOnly,
  removeGroupMember,
  renameGroup,
  type Member,
  type UserGroup,
} from '../../../api/workspace';
import { Toolbar } from '../AdminPage';
import { RowActions } from '../common';
import { useAction } from '../useAction';
import { Sheet } from '../../../ui/Sheet';
import { Skeleton } from '../../../ui/Skeleton';
import { Table, type Column } from '../../../ui/Table';
import { NameDialog } from '../NameDialog';
import { KEYS, useGroups, useMembers } from '../queries';
import { nameOf } from './helpers';
import { MemberSelect } from './MemberSelect';

function GroupMembers({
  group,
  members,
  onClose,
}: {
  readonly group: UserGroup;
  readonly members: readonly Member[];
  readonly onClose: () => void;
}) {
  const [userId, setUserId] = useState<string>();
  const invalidate = [KEYS.groups];
  const add = useAction((id: string) => addGroupMember(group.id, id), {
    invalidate,
    success: '已加入用户组',
    failure: '加入没有成功，请稍后重试',
  });
  const remove = useAction((id: string) => removeGroupMember(group.id, id), {
    invalidate,
    success: '已移出用户组',
    failure: '移出没有成功，请稍后重试',
  });
  const columns: readonly Column<{ readonly id: string }>[] = [
    { key: 'member', title: '成员', ellipsis: true, render: (row) => nameOf(members, row.id) },
    {
      key: 'actions',
      title: '操作',
      width: 90,
      render: (row) => (
        <RowActions
          actions={[
            {
              key: 'remove',
              label: '移出',
              danger: true,
              confirm: '把这位成员移出用户组？',
              onClick: () => void remove.run(row.id),
            },
          ]}
        />
      ),
    },
  ];
  return (
    <Sheet title={`用户组「${group.name}」的成员`} width={520} onClose={onClose}>
      <div className="kw-inline-form">
        <MemberSelect
          members={members}
          exclude={new Set(group.members)}
          value={userId}
          onChange={setUserId}
        />
        <Button
          color="secondary"
          variant="outline"
          disabled={!userId}
          loading={add.pending}
          onClick={() => userId && void add.run(userId).then((ok) => ok && setUserId(undefined))}
        >
          加入
        </Button>
      </div>
      <Table
        className="kw-org-members"
        rowKey={(row) => row.id}
        columns={columns}
        rows={group.members.map((id) => ({ id }))}
        pageSize={false}
        empty="还没有成员"
      />
    </Sheet>
  );
}

type Dialog =
  { readonly type: 'create' } | { readonly type: 'rename' | 'members'; readonly group: UserGroup };

export function GroupsTab() {
  const groups = useGroups();
  const members = useMembers();
  const [dialog, setDialog] = useState<Dialog | null>(null);
  const invalidate = [KEYS.groups];
  const create = useAction((name: string) => createGroup(name), {
    invalidate,
    success: '已新建用户组',
    failure: '新建没有成功，请稍后重试',
  });
  const rename = useAction(({ id, name }: { id: string; name: string }) => renameGroup(id, name), {
    invalidate,
    success: '已重命名',
    failure: '重命名没有成功，请稍后重试',
  });
  const remove = useAction((id: string) => deleteGroup(id), {
    invalidate,
    success: '已删除用户组',
    failure: '删除没有成功，请稍后重试',
  });
  const current =
    dialog && dialog.type !== 'create'
      ? ((groups.data ?? []).find((g) => g.id === dialog.group.id) ?? dialog.group)
      : undefined;

  const columns: readonly Column<UserGroup>[] = [
    { key: 'name', title: '名称', ellipsis: true, render: (g) => g.name },
    {
      key: 'count',
      title: '成员数',
      width: 100,
      align: 'right',
      render: (g) => <span className="kw-num">{g.members.length}</span>,
    },
    {
      key: 'actions',
      title: '操作',
      width: 200,
      render: (g) => (
        <RowActions
          actions={[
            { key: 'members', label: '成员', onClick: () => setDialog({ type: 'members', group: g }) },
            { key: 'rename', label: '重命名', onClick: () => setDialog({ type: 'rename', group: g }) },
            {
              key: 'delete',
              label: '删除',
              danger: true,
              confirm: `删除用户组「${g.name}」？授给它的权限会一并撤销。`,
              onClick: () => void remove.run(g.id),
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
          <Button color="primary" onClick={() => setDialog({ type: 'create' })}>
            <Plus />
            新建用户组
          </Button>
        }
      />
      {groups.isPending ? (
        <Skeleton rows={6} />
      ) : (
        <Table<UserGroup>
          rowKey={(g) => g.id}
          columns={columns}
          rows={groups.data ?? []}
          loading={groups.isFetching}
          empty={groups.isError ? '用户组加载失败，请刷新重试' : '还没有用户组'}
        />
      )}
      {dialog?.type === 'create' ? (
        <NameDialog title="新建用户组" okText="新建" onSubmit={create.run} onClose={() => setDialog(null)} />
      ) : null}
      {dialog?.type === 'rename' && current ? (
        <NameDialog
          title="重命名用户组"
          okText="保存"
          initial={current.name}
          onSubmit={(name) => rename.run({ id: current.id, name })}
          onClose={() => setDialog(null)}
        />
      ) : null}
      {dialog?.type === 'members' && current ? (
        <GroupMembers
          group={current}
          members={peopleOnly(members.data ?? [])}
          onClose={() => setDialog(null)}
        />
      ) : null}
    </>
  );
}
