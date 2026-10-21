/** 只填一个名称的对话框（新建、重命名部门与用户组等）。 */
import { useState } from 'react';
import { Input } from '@openai/apps-sdk-ui/components/Input';
import { Dialog, DialogActions } from '../../ui/Dialog';
import { Field } from '../../ui/Field';

interface Props {
  readonly title: string;
  readonly okText: string;
  readonly initial?: string;
  readonly label?: string;
  readonly maxLength?: number;
  /** 返回 true 表示成功，对话框关闭。 */
  readonly onSubmit: (name: string) => Promise<boolean>;
  readonly onClose: () => void;
}

export function NameDialog({
  title,
  okText,
  initial = '',
  label = '名称',
  maxLength = 100,
  onSubmit,
  onClose,
}: Props) {
  const [name, setName] = useState(initial);
  const [problem, setProblem] = useState<string>();
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    const value = name.trim();
    setProblem(value ? undefined : `请输入${label}`);
    if (!value) return;
    setBusy(true);
    try {
      if (await onSubmit(value)) onClose();
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog
      title={title}
      onClose={onClose}
      onSubmit={() => void submit()}
      footer={<DialogActions okText={okText} loading={busy} onCancel={onClose} />}
    >
      <Field label={label} error={problem}>
        {(id) => (
          <Input
            id={id}
            value={name}
            maxLength={maxLength}
            invalid={Boolean(problem)}
            onChange={(e) => setName(e.target.value)}
            autoFocus
          />
        )}
      </Field>
    </Dialog>
  );
}
