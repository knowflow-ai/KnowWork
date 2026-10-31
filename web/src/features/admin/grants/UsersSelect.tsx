/**
 * 「给谁用」：技能、MCP 服务在自己的设置里选给哪些对象用（全体成员、部门、用户组、人），不用去资源授权页一条条加。
 * 存的是资源上「可用」一档的授权（接口 PUT /grants/users）；可编辑、可管理两档仍在资源授权页里改。
 */
import { useState } from 'react';
import { Dialog, DialogActions } from '../../../ui/Dialog';
import { Field } from '../../../ui/Field';
import { TreeSelect } from '../../../ui/TreeSelect';
import { useSubjectTree } from './users';

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
    <Field label={label} help="不选就只有管理员能用。所有者、管理员本来就能用全部。">
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
