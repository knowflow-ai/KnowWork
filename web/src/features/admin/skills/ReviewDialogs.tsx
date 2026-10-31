/**
 * 审核技能提交的两个对话框。
 * - 通过：第一次进空间时一并选「给谁用」（成员默认没有技能的权限，不授权就只有管理员能用）和要不要直接发布；
 *   以后的版本保留原来的授权，只问要不要直接发布。
 * - 不通过：必须写原因，作者在「我的技能」里能看到。
 */
import { useState } from 'react';
import { Checkbox } from '@openai/apps-sdk-ui/components/Checkbox';
import { Textarea } from '@openai/apps-sdk-ui/components/Textarea';
import type { SubmissionDetail } from '../../../api/skills';
import { Dialog, DialogActions } from '../../../ui/Dialog';
import { Field } from '../../../ui/Field';
import { EVERYONE } from '../grants/users';
import { UsersSelect } from '../grants/UsersSelect';
import { useSubmissionActions } from './queries';

const MAX_REASON = 500;

export function ApproveDialog({
  detail,
  onDone,
}: {
  readonly detail: SubmissionDetail;
  readonly onDone: (ok: boolean) => void;
}) {
  const { approve } = useSubmissionActions();
  const first = detail.target === null;
  const [grantTo, setGrantTo] = useState<string[]>([EVERYONE]);
  const [publish, setPublish] = useState(false);
  const submit = async () => {
    const ok = await approve.run({ id: detail.submission.id, publish, grantTo: first ? grantTo : null });
    if (ok) onDone(true);
  };
  return (
    <Dialog
      title={`通过「${detail.submission.name}」`}
      onClose={() => onDone(false)}
      onSubmit={() => void submit()}
      footer={<DialogActions okText="通过" loading={approve.pending} onCancel={() => onDone(false)} />}
    >
      <p className="kw-secondary-text">
        {first
          ? '通过后在空间里新建这个技能，默认全体成员可用；也可以只给部分人，之后在技能详情里随时改。'
          : '通过后成为空间里这个技能的新版本，原来的授权不变。'}
      </p>
      {first ? <UsersSelect value={grantTo} onChange={setGrantTo} /> : null}
      <Checkbox
        label="直接发布（不勾就先进草稿，在技能列表里点「发布」后才生效）"
        checked={publish}
        onCheckedChange={(checked) => setPublish(checked === true)}
      />
    </Dialog>
  );
}

export function RejectDialog({
  detail,
  onDone,
}: {
  readonly detail: SubmissionDetail;
  readonly onDone: (ok: boolean) => void;
}) {
  const { reject } = useSubmissionActions();
  const [reason, setReason] = useState('');
  const [problem, setProblem] = useState<string>();
  const submit = async () => {
    const value = reason.trim();
    setProblem(value ? undefined : '请写不通过的原因');
    if (!value) return;
    if (await reject.run({ id: detail.submission.id, reason: value })) onDone(true);
  };
  return (
    <Dialog
      title={`不通过「${detail.submission.name}」`}
      onClose={() => onDone(false)}
      onSubmit={() => void submit()}
      footer={<DialogActions okText="退回" loading={reject.pending} onCancel={() => onDone(false)} />}
    >
      <Field label="原因" help="作者在「我的技能」里能看到，写清楚哪里要改。" error={problem}>
        {(id) => (
          <Textarea
            id={id}
            rows={4}
            maxLength={MAX_REASON}
            placeholder="例如：和已有的「合同初审」重复，建议合并后再提交"
            value={reason}
            onChange={(event) => setReason(event.target.value)}
          />
        )}
      </Field>
    </Dialog>
  );
}
