/** 成员页的对话框：添加成员、改角色、邀请新用户（邀请链接只显示这一次）。 */
import { useState } from 'react';
import { Input } from '@openai/apps-sdk-ui/components/Input';
import { Select } from '@openai/apps-sdk-ui/components/Select';
import { inviteUser, type Invitation } from '../../../api/identity';
import { validateEmail } from '../../../api/auth';
import { readableError } from '../../../api/http';
import { addMember, memberLabel, ROLES, setMemberRole, type Member, type Role } from '../../../api/workspace';
import { Dialog, DialogActions } from '../../../ui/Dialog';
import { Field } from '../../../ui/Field';
import { SecretText } from '../../../ui/SecretText';
import { toast } from '../../../ui/toastStore';
import { useAction } from '../useAction';
import { KEYS } from '../queries';
import { UserPicker } from './UserPicker';

const ROLE_OPTIONS = ROLES.map((r) => ({ value: r.value, label: r.label }));

function RoleSelect({ value, onChange }: { readonly value: Role; readonly onChange: (role: Role) => void }) {
  return (
    <Select
      block
      options={ROLE_OPTIONS}
      value={value}
      onChange={(option) => onChange(option.value as Role)}
    />
  );
}

export function AddMemberDialog({
  members,
  onClose,
}: {
  readonly members: readonly Member[];
  readonly onClose: () => void;
}) {
  const [userId, setUserId] = useState<string>();
  const [role, setRole] = useState<Role>('editor');
  const [missing, setMissing] = useState(false);
  const action = useAction(({ id, r }: { id: string; r: Role }) => addMember(id, r), {
    invalidate: [KEYS.members],
    success: '已添加成员',
    failure: '添加没有成功，请稍后重试',
  });
  const submit = async () => {
    setMissing(!userId);
    if (!userId) return;
    if (await action.run({ id: userId, r: role })) onClose();
  };
  return (
    <Dialog
      title="添加成员"
      onClose={onClose}
      onSubmit={() => void submit()}
      footer={<DialogActions okText="添加" loading={action.pending} onCancel={onClose} />}
    >
      <Field
        label="用户"
        help="只能添加已注册的账号；还没有账号的同事请用「邀请新用户」"
        error={missing ? '请选择要添加的用户' : undefined}
      >
        {(id) => (
          <UserPicker
            id={id}
            invalid={missing}
            onChange={(next) => {
              setUserId(next);
              if (next) setMissing(false);
            }}
            exclude={new Set(members.map((m) => m.userId))}
          />
        )}
      </Field>
      <Field label="角色">
        <RoleSelect value={role} onChange={setRole} />
      </Field>
    </Dialog>
  );
}

export function RoleDialog({ member, onClose }: { readonly member: Member; readonly onClose: () => void }) {
  const [role, setRole] = useState<Role>(member.role);
  const action = useAction((r: Role) => setMemberRole(member.userId, r), {
    invalidate: [KEYS.members],
    success: '角色已更新',
    failure: '改角色没有成功，请稍后重试',
  });
  const submit = async () => {
    if (role === member.role) return onClose();
    if (await action.run(role)) onClose();
  };
  return (
    <Dialog
      title={`修改「${memberLabel(member)}」的角色`}
      onClose={onClose}
      onSubmit={() => void submit()}
      footer={<DialogActions okText="保存" loading={action.pending} onCancel={onClose} />}
    >
      <Field label="角色" help="所有者能管理所有者；空间至少保留一位所有者">
        <RoleSelect value={role} onChange={setRole} />
      </Field>
    </Dialog>
  );
}

export function InviteDialog({ onClose }: { readonly onClose: () => void }) {
  const [email, setEmail] = useState('');
  const [problem, setProblem] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [issued, setIssued] = useState<Invitation | null>(null);
  if (issued) {
    return (
      <Dialog
        title="邀请新用户"
        description={`把链接发给 ${issued.email}。链接只显示这一次，对方注册后会自动加入默认空间。`}
        onClose={onClose}
        closeOnOutside={false}
        footer={<DialogActions okText="完成" onOk={onClose} />}
      >
        <SecretText value={issued.url} />
      </Dialog>
    );
  }
  const submit = async () => {
    const invalid = validateEmail(email);
    setProblem(invalid ?? undefined);
    if (invalid) return;
    setBusy(true);
    try {
      setIssued(await inviteUser(email));
    } catch (error) {
      toast.error(readableError(error, '邀请没有成功，请稍后重试'));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog
      title="邀请新用户"
      onClose={onClose}
      onSubmit={() => void submit()}
      footer={<DialogActions okText="生成邀请链接" loading={busy} onCancel={onClose} />}
    >
      <Field label="邮箱" error={problem}>
        {(id) => (
          <Input
            id={id}
            value={email}
            invalid={Boolean(problem)}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="name@company.com"
            maxLength={320}
            autoFocus
          />
        )}
      </Field>
    </Dialog>
  );
}
