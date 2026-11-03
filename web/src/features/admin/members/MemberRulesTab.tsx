/**
 * 成员规则：组织架构里某个部门（可含下级）的人自动是本空间成员。人离开部门时随之移出，手工加的成员不受影响；
 * 一人命中多条取最高的角色。只在接了企业目录（有组织架构）时出现。
 */
import { useMemo, useState } from 'react';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { Plus } from '@openai/apps-sdk-ui/components/Icon';
import { SegmentedControl } from '@openai/apps-sdk-ui/components/SegmentedControl';
import { Switch } from '@openai/apps-sdk-ui/components/Switch';
import {
  createMemberRule,
  deleteMemberRule,
  orgPath,
  orgTree,
  type MemberRule,
  type MemberRuleInput,
  type OrgNode,
  type RuleRole,
} from '../../../api/org';
import { Dialog, DialogActions } from '../../../ui/Dialog';
import { Field } from '../../../ui/Field';
import { Skeleton } from '../../../ui/Skeleton';
import { Table, type Column } from '../../../ui/Table';
import { TreeSelect, type TreeSelectNode } from '../../../ui/TreeSelect';
import { Toolbar } from '../AdminPage';
import { RowActions } from '../common';
import { KEYS, useMemberRules, useOrgDepartments } from '../queries';
import { useAction } from '../useAction';

const ROLE_TEXT: Record<RuleRole, string> = { editor: '成员', admin: '管理员' };

function nodes(tree: readonly OrgNode[]): TreeSelectNode[] {
  return tree.map((n) => ({
    key: n.department.id,
    label: `${n.department.name}（${n.department.memberCount} 人）`,
    children: nodes(n.children),
  }));
}

function RuleDialog({ onClose }: { readonly onClose: () => void }) {
  const departments = useOrgDepartments();
  const tree = useMemo(() => nodes(orgTree(departments.data ?? [])), [departments.data]);
  const [departmentId, setDepartmentId] = useState('');
  const [includeChildren, setIncludeChildren] = useState(true);
  const [role, setRole] = useState<RuleRole>('editor');
  const [problem, setProblem] = useState<string | null>(null);
  const action = useAction((input: MemberRuleInput) => createMemberRule(input), {
    invalidate: [KEYS.memberRules, KEYS.members],
    success: '已添加规则，这个部门的人已加进本空间',
    failure: '添加没有成功，请稍后重试',
  });
  const submit = async () => {
    if (!departmentId) {
      setProblem('请从组织架构里选部门');
      return;
    }
    if (await action.run({ departmentId, includeChildren, role })) onClose();
  };
  return (
    <Dialog
      title="添加成员规则"
      onClose={onClose}
      onSubmit={() => void submit()}
      footer={<DialogActions okText="添加" loading={action.pending} onCancel={onClose} />}
    >
      <Field label="部门" error={problem ?? undefined} help="在组织架构里选，组织架构从企业目录同步来">
        <TreeSelect
          aria-label="部门"
          nodes={tree}
          value={departmentId}
          placeholder="选择部门"
          searchPlaceholder="搜索部门"
          emptyMessage="没有匹配的部门"
          invalid={Boolean(problem)}
          onChange={(key) => {
            setDepartmentId(key);
            setProblem(null);
          }}
        />
      </Field>
      <div className="kw-field">
        <Switch label="包括下级部门" checked={includeChildren} onCheckedChange={setIncludeChildren} />
      </div>
      <Field label="角色" help="管理员能管理本空间的成员、授权与资源">
        <SegmentedControl<RuleRole> aria-label="角色" value={role} onChange={setRole}>
          <SegmentedControl.Option value="editor">成员</SegmentedControl.Option>
          <SegmentedControl.Option value="admin">管理员</SegmentedControl.Option>
        </SegmentedControl>
      </Field>
    </Dialog>
  );
}

export function MemberRulesTab() {
  const rules = useMemberRules();
  const departments = useOrgDepartments();
  const [adding, setAdding] = useState(false);
  const remove = useAction((id: string) => deleteMemberRule(id), {
    invalidate: [KEYS.memberRules, KEYS.members],
    success: '已删除规则',
    failure: '删除没有成功，请稍后重试',
  });
  const name = (rule: MemberRule) =>
    rule.departmentActive
      ? orgPath(rule.departmentId, departments.data ?? []) || rule.departmentName
      : rule.departmentName;
  const columns: readonly Column<MemberRule>[] = [
    {
      key: 'department',
      title: '部门（组织架构）',
      ellipsis: true,
      render: (rule) => (
        <span className="kw-cell-stack">
          <span>{name(rule)}</span>
          {rule.departmentActive ? null : <span className="kw-secondary">已从目录移除，不再加人</span>}
        </span>
      ),
    },
    {
      key: 'children',
      title: '包括下级',
      width: 100,
      render: (rule) => (rule.includeChildren ? '是' : '否'),
    },
    { key: 'role', title: '角色', width: 100, render: (rule) => ROLE_TEXT[rule.role] },
    {
      key: 'actions',
      title: '操作',
      width: 100,
      render: (rule) => (
        <RowActions
          actions={[
            {
              key: 'delete',
              label: '删除',
              danger: true,
              confirm: `删除「${rule.departmentName}」这条规则？只靠这条规则进来的人会被移出本空间，手工加的成员不受影响。`,
              onClick: () => void remove.run(rule.id),
            },
          ]}
        />
      ),
    },
  ];
  return (
    <>
      <Toolbar
        left={<span className="kw-secondary">命中规则的人自动是本空间成员，离开部门时随之移出</span>}
        right={
          <Button color="primary" onClick={() => setAdding(true)}>
            <Plus />
            添加规则
          </Button>
        }
      />
      {rules.isPending ? (
        <Skeleton rows={3} />
      ) : (
        <Table<MemberRule>
          rowKey={(rule) => rule.id}
          columns={columns}
          rows={rules.data ?? []}
          loading={rules.isFetching}
          empty={rules.isError ? '成员规则加载失败，请刷新重试' : '还没有成员规则，成员都是手工加的'}
        />
      )}
      {adding ? <RuleDialog onClose={() => setAdding(false)} /> : null}
    </>
  );
}
