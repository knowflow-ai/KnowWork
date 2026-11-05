/**
 * 「给谁用」：技能、MCP 服务在自己的设置里选给哪些对象用（全体成员、部门、用户组、人），不用去资源授权页一条条加。
 * 存的是资源上「可用」一档的授权（接口 PUT /grants/users），整体替换：选了具体的对象、没选全体成员，全体成员就不再能用。
 * 下面那行字按现在选的写清后果（2026-11-04 用户问「改完是不是全员失效」：管理员容易以为是在全员之上再加人）。
 * 可编辑、可管理两档仍在资源授权页里改。
 */
import { useState } from 'react';
import { Dialog, DialogActions } from '../../../ui/Dialog';
import { Field } from '../../../ui/Field';
import { TreeSelect } from '../../../ui/TreeSelect';
import { EVERYONE, useSubjectTree } from './users';

/** 现在这样选，谁能用 */
function effectOf(value: readonly string[]): string {
  if (value.length === 0) return '不选就只有管理员能用。';
  if (value.includes(EVERYONE)) return '全体成员都能用。';
  return '只有选中的对象和管理员能用，其他成员不再能用。';
}

export function UsersSelect({
  value,
  onChange,
  label = '给谁用',
}: {
  readonly value: readonly string[];
  readonly onChange: (subjects: string[]) => void;
  readonly label?: string;
}) {
  const nodes = useSubjectTree();
  return (
    <Field label={label} help={`${effectOf(value)}所有者、管理员本来就能用全部。`}>
      {/* 树形多选：部门按层级展开，和授权页挑知识库、工具的控件同一个 */}
      <TreeSelect
        multiple
        aria-label={label}
        nodes={nodes}
        value={value}
        onChange={onChange}
        placeholder="只有管理员"
        searchPlaceholder="搜索部门、用户组或成员"
        emptyMessage="没有匹配的对象"
      />
    </Field>
  );
}

/** 修改「给谁用」的对话框。onSave 返回是否保存成功。 */
export function UsersDialog({
  title,
  initial,
  pending,
  onSave,
  onClose,
}: {
  readonly title: string;
  readonly initial: readonly string[];
  readonly pending: boolean;
  readonly onSave: (subjects: string[]) => Promise<boolean>;
  readonly onClose: () => void;
}) {
  const [value, setValue] = useState<string[]>([...initial]);
  const submit = async () => {
    if (await onSave(value)) onClose();
  };
  return (
    <Dialog
      title={title}
      onClose={onClose}
      onSubmit={() => void submit()}
      footer={<DialogActions okText="保存" loading={pending} onCancel={onClose} />}
    >
      <UsersSelect value={value} onChange={setValue} />
    </Dialog>
  );
}
