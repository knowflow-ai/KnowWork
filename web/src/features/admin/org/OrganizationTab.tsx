/**
 * 组织架构：从企业目录（AD、企业微信等，管理后台「组织同步」）同步来的部门树，只读。左侧树、右侧选中部门的人，
 * 标出谁在本空间、什么角色。要改人和部门去目录里改；要让一个部门的人进本空间，用「成员 → 成员规则」。
 */
import { useMemo, useState } from 'react';
import { Users } from '@openai/apps-sdk-ui/components/Icon';
import { orgPath, orgTree, type OrgMember, type OrgNode } from '../../../api/org';
import { roleLabel } from '../../../api/workspace';
import { Empty } from '../../../ui/Empty';
import { Table, type Column } from '../../../ui/Table';
import { Tree, type TreeNode } from '../../../ui/Tree';
import { useOrgDepartmentMembers, useOrgDepartments } from '../queries';

function toTree(nodes: readonly OrgNode[]): TreeNode[] {
  return nodes.map((node) => ({
    key: node.department.id,
    title: `${node.department.name}（${node.department.memberCount}）`,
    children: toTree(node.children),
  }));
}

const COLUMNS: readonly Column<OrgMember>[] = [
  { key: 'name', title: '成员', ellipsis: true, render: (row) => row.name },
  { key: 'primary', title: '归属', width: 120, render: (row) => (row.isPrimary ? '主部门' : '兼任') },
  {
    key: 'space',
    title: '本空间',
    width: 140,
    render: (row) => (row.role ? roleLabel(row.role) : <span className="kw-secondary">不在本空间</span>),
  },
];

function OrgPanel({ id, path }: { readonly id: string; readonly path: string }) {
  const members = useOrgDepartmentMembers(id);
  return (
    <section className="kw-org-panel" aria-label={`组织架构：${path}`}>
      <div className="kw-org-panel-head">
        <h2 className="kw-org-panel-title">{path}</h2>
      </div>
      <div className="kw-field-note">
        从企业目录同步来，以目录为准，这里只能看；只列这一级的人，下级部门的人在下级里看
      </div>
      <Table<OrgMember>
        className="kw-org-members"
        rowKey={(row) => row.userId}
        columns={COLUMNS}
        rows={members.data ?? []}
        loading={members.isPending}
        pageSize={false}
        empty={members.isError ? '成员加载失败，请刷新重试' : '这一级没有人'}
      />
    </section>
  );
}

export function OrganizationTab() {
  const departments = useOrgDepartments();
  const [selected, setSelected] = useState<string>();
  const list = useMemo(() => departments.data ?? [], [departments.data]);
  const tree = useMemo(() => toTree(orgTree(list)), [list]);
  return (
    <div className="kw-org-layout">
      <Tree
        className="kw-org-tree"
        aria-label="组织架构"
        nodes={tree}
        selected={selected}
        onSelect={setSelected}
      />
      {selected && list.some((d) => d.id === selected) ? (
        <OrgPanel id={selected} path={orgPath(selected, list)} />
      ) : (
        <Empty area icon={<Users />} description="选择左侧的部门查看成员" />
      )}
    </div>
  );
}
