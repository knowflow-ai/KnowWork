/** 创建 API Key：明文只在创建成功后显示这一次。 */
import { useState } from 'react';
import { Checkbox } from '@openai/apps-sdk-ui/components/Checkbox';
import { Input } from '@openai/apps-sdk-ui/components/Input';
import { Select } from '@openai/apps-sdk-ui/components/Select';
import { API_KEY_SCOPES, createApiKey, validateApiKeyInput, type IssuedApiKey } from '../../api/identity';
import { readableError } from '../../api/http';
import { WORKSPACE_ID } from '../../api/workspace';
import { Dialog, DialogActions } from '../../ui/Dialog';
import { Field } from '../../ui/Field';
import { rule, useForm, type Rules } from '../../ui/form';
import { SecretText } from '../../ui/SecretText';
import { toast } from '../../ui/toastStore';
import { EXPIRY_OPTIONS, expiryFrom } from './expiry';

interface Values {
  readonly name: string;
  readonly scopes: readonly string[];
  readonly days: number;
  readonly bind: boolean;
}

const RULES: Rules<Values> = {
  name: rule.all<Values, 'name'>(rule.required('请输入名称'), rule.maxLength(100, '名称不能超过 100 个字')),
  scopes: rule.required('至少选择一项权限范围'),
};

const DAYS = EXPIRY_OPTIONS.map((o) => ({ value: String(o.value), label: o.label }));

function IssuedKey({ issued, onClose }: { readonly issued: IssuedApiKey; readonly onClose: () => void }) {
  return (
    <Dialog
      title={`已创建「${issued.name}」`}
      description="请现在复制保存，关闭后无法再次查看"
      onClose={onClose}
      closeOnOutside={false}
      footer={<DialogActions okText="我已保存" onOk={onClose} />}
    >
      <SecretText value={issued.key} />
    </Dialog>
  );
}

export function CreateApiKeyDialog({
  onCreated,
  onClose,
}: {
  readonly onCreated: () => void;
  readonly onClose: () => void;
}) {
  const form = useForm<Values>({ name: '', scopes: ['conversations:use'], days: 90, bind: true }, RULES);
  const [busy, setBusy] = useState(false);
  const [issued, setIssued] = useState<IssuedApiKey | null>(null);
  if (issued) return <IssuedKey issued={issued} onClose={onClose} />;

  const submit = async () => {
    const values = form.validate();
    if (!values) return;
    const input = {
      name: values.name,
      scopes: [...values.scopes],
      expiresAt: expiryFrom(values.days),
      workspaceId: values.bind ? WORKSPACE_ID : null,
    };
    const problem = validateApiKeyInput(input);
    if (problem) {
      toast.warning(problem);
      return;
    }
    setBusy(true);
    try {
      setIssued(await createApiKey(input));
      onCreated();
    } catch (error) {
      toast.error(readableError(error, '创建没有成功，请稍后重试'));
    } finally {
      setBusy(false);
    }
  };
  const { values, errors, set } = form;
  const toggleScope = (scope: string, checked: boolean) =>
    set('scopes', checked ? [...values.scopes, scope] : values.scopes.filter((s) => s !== scope));

  return (
    <Dialog
      title="创建 API Key"
      onClose={onClose}
      onSubmit={() => void submit()}
      footer={<DialogActions okText="创建" loading={busy} onCancel={onClose} />}
    >
      <Field label="名称" error={errors.name}>
        {(id) => <Input id={id} placeholder="例如：报表脚本" {...form.text('name')} />}
      </Field>
      <Field label="权限范围" help="Key 的权限不会超过你本人在空间里的权限" error={errors.scopes}>
        <div className="kw-check-list">
          {API_KEY_SCOPES.map((scope) => (
            <Checkbox
              key={scope.value}
              label={scope.label}
              checked={values.scopes.includes(scope.value)}
              onCheckedChange={(checked) => toggleScope(scope.value, checked)}
            />
          ))}
        </div>
      </Field>
      <Field label="有效期">
        <Select
          options={DAYS}
          value={String(values.days)}
          onChange={(option) => set('days', Number(option.value))}
          block
        />
      </Field>
      <Checkbox
        label="只允许在默认空间使用"
        checked={values.bind}
        onCheckedChange={(checked) => set('bind', checked)}
      />
    </Dialog>
  );
}
