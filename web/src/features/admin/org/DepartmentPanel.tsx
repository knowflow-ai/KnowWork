/** 选中部门后的右侧面板：子部门、重命名、删除，以及部门成员的加入与移出。 */
import { useState } from 'react';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { Checkbox } from '@openai/apps-sdk-ui/components/Checkbox';
import {
  addDepartmentMember,
  createDepartment,
  deleteDepartment,
  removeDepartmentMember,
  renameDepartment,
  type Department,
  type DepartmentMember,
  type Member,
} from '../../../api/workspace';
import { RowActions } from '../common';
import { useAction } from '../useAction';
import { Table, type Column } from '../../../ui/Table';
import { NameDialog } from '../NameDialog';
import { KEYS } from '../queries';
import { nameOf } from './helpers';
import { MemberSelect } from './MemberSelect';

interface Props {
  readonly department: Department;
  readonly members: readonly Member[];
  readonly onDeleted: () => void;
}

function AddDepartmentMember({ department, members }: Omit<Props, 'onDeleted'>) {
  const [userId, setUserId] = useState<string>();
  const [primary, setPrimary] = useState(false);
  const add = useAction(
    ({ id, p }: { id: string; p: boolean }) => addDepartmentMember(department.id, id, p),
    {
      invalidate: [KEYS.departments],
      success: '已加入部门',
      failure: '加入没有成功，请稍后重试',
    },
  );
  const submit = async () => {
    if (userId && (await add.run({ id: userId, p: primary }))) setUserId(undefined);
  };
  return (
    <div className="kw-inline-form">
      <MemberSelect
        members={members}
        exclude={new Set(department.members.map((m) => m.userId))}
        value={userId}
        onChange={setUserId}
      />
      <Button
        color="secondary"
        variant="outline"
        onClick={() => void submit()}
        disabled={!userId}
        loading={add.pending}
      >
        加入部门
      </Button>
      <Checkbox checked={primary} onCheckedChange={setPrimary} label="设为主部门" />
    </div>
  );
}

export function DepartmentPanel({ department, members, onDeleted }: Props) {
  const [dialog, setDialog] = useState<'child' | 'rename' | null>(null);
  const invalidate = [KEYS.departments];
  const create = useAction((name: string) => createDepartment(name, department.id), {
    invalidate,
    success: '已新建子部门',
    failure: '新建没有成功，请稍后重试',
  });
  const rename = useAction((name: string) => renameDepartment(department.id, name), {
    invalidate,
    success: '已重命名',
    failure: '重命名没有成功，请稍后重试',
  });
  const remove = useAction(() => deleteDepartment(department.id), {
    invalidate,
    success: '已删除部门',
    failure: '删除没有成功，请稍后重试',
  });
  const removeMember = useAction((userId: string) => removeDepartmentMember(department.id, userId), {
    invalidate,
    success: '已移出部门',
    failure: '移出没有成功，请稍后重试',
  });

  const columns: readonly Column<DepartmentMember>[] = [
    { key: 'member', title: '成员', ellipsis: true, render: (row) => nameOf(members, row.userId) },
    { key: 'primary', title: '归属', width: 120, render: (row) => (row.isPrimary ? '主部门' : '兼任') },
    {
      key: 'actions',
      title: '操作',
      width: 100,
      render: (row) => (
        <RowActions
          actions={[
            {
              key: 'remove',
              label: '移出',
              danger: true,
              confirm: `把「${nameOf(members, row.userId)}」移出这个部门？`,
              onClick: () => void removeMember.run(row.userId),
            },
          ]}
        />
      ),
    },
  ];

  return (
    <section className="kw-org-panel" aria-label={`部门：${department.name}`}>
      <div className="kw-org-panel-head">
        <h2 className="kw-org-panel-title">{department.name}</h2>
        <RowActions
          actions={[
            { key: 'child', label: '新建子部门', onClick: () => setDialog('child') },
            { key: 'rename', label: '重命名', onClick: () => setDialog('rename') },
            {
              key: 'delete',
              label: '删除',
              danger: true,
              confirm: `删除部门「${department.name}」？有子部门时不能删除。`,
              onClick: () => void remove.run(undefined).then((ok) => ok && onDeleted()),
            },
          ]}
        />
      </div>
      <AddDepartmentMember department={department} members={members} />
      <Table<DepartmentMember>
        className="kw-org-members"
        rowKey={(row) => row.userId}
        columns={columns}
        rows={department.members}
        pageSize={false}
        empty="这个部门还没有成员"
      />
      {dialog === 'child' ? (
        <NameDialog
          title={`在「${department.name}」下新建部门`}
          okText="新建"
          onSubmit={create.run}
          onClose={() => setDialog(null)}
        />
      ) : null}
      {dialog === 'rename' ? (
        <NameDialog
          title="重命名部门"
          okText="保存"
          initial={department.name}
          onSubmit={rename.run}
          onClose={() => setDialog(null)}
        />
      ) : null}
    </section>
  );
}
