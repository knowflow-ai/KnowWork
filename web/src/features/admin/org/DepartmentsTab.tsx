/** 部门树：左侧树、右侧选中部门的详情。 */
import { useMemo, useState } from 'react';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { Plus } from '@openai/apps-sdk-ui/components/Icon';
import { createDepartment, findDepartment } from '../../../api/workspace';
import { Empty } from '../../../ui/Empty';
import { Skeleton } from '../../../ui/Skeleton';
import { Tree } from '../../../ui/Tree';
import { Toolbar } from '../AdminPage';
import { useAction } from '../useAction';
import { NameDialog } from '../NameDialog';
import { KEYS, useDepartments, useMembers } from '../queries';
import { DepartmentPanel } from './DepartmentPanel';
import { toTreeData } from './helpers';

export function DepartmentsTab() {
  const departments = useDepartments();
  const members = useMembers();
  const [selected, setSelected] = useState<string>();
  const [creating, setCreating] = useState(false);
  const create = useAction((name: string) => createDepartment(name, null), {
    invalidate: [KEYS.departments],
    success: '已新建部门',
    failure: '新建没有成功，请稍后重试',
  });
  const tree = useMemo(() => toTreeData(departments.data ?? []), [departments.data]);
  const current = selected ? findDepartment(departments.data ?? [], selected) : undefined;

  if (departments.isPending) return <Skeleton rows={6} />;
  return (
    <>
      <Toolbar
        right={
          <Button color="primary" onClick={() => setCreating(true)}>
            <Plus />
            新建部门
          </Button>
        }
      />
      {tree.length === 0 ? (
        <Empty description={departments.isError ? '部门加载失败，请刷新重试' : '还没有部门'} />
      ) : (
        <div className="kw-org-layout">
          <Tree
            className="kw-org-tree"
            aria-label="部门"
            nodes={tree}
            selected={selected}
            onSelect={setSelected}
          />
          {current ? (
            <DepartmentPanel
              department={current}
              members={members.data ?? []}
              onDeleted={() => setSelected(undefined)}
            />
          ) : (
            <Empty description="选择左侧的部门查看成员" />
          )}
        </div>
      )}
      {creating ? (
        <NameDialog title="新建部门" okText="新建" onSubmit={create.run} onClose={() => setCreating(false)} />
      ) : null}
    </>
  );
}
