/** 新建或修改模型厂商。密钥只能写不能读：修改时留空表示不改，勾选「清除密钥」才清掉。 */
import { useState } from 'react';
import { Checkbox } from '@openai/apps-sdk-ui/components/Checkbox';
import { Input } from '@openai/apps-sdk-ui/components/Input';
import { Switch } from '@openai/apps-sdk-ui/components/Switch';
import {
  createProvider,
  updateProvider,
  validateBaseUrl,
  type Provider,
  type ProviderInput,
} from '../../../api/models';
import { Dialog, DialogActions } from '../../../ui/Dialog';
import { Field } from '../../../ui/Field';
import { rule, useForm, type Rules } from '../../../ui/form';
import { useAction } from '../useAction';
import { providerInput, type Values } from './providerForm';
import { KEYS } from '../queries';

const RULES: Rules<Values> = {
  name: rule.all<Values, 'name'>(rule.required('请输入名称'), rule.maxLength(100, '名称不能超过 100 个字')),
  baseUrl: (value) => validateBaseUrl(value) ?? undefined,
  apiKey: rule.maxLength(1000, '密钥过长'),
};

export function ProviderDialog({
  provider,
  onClose,
}: {
  readonly provider?: Provider;
  readonly onClose: () => void;
}) {
  const [clearKey, setClearKey] = useState(false);
  const editing = provider !== undefined;
  const form = useForm<Values>(
    {
      name: provider?.name ?? '',
      baseUrl: provider?.baseUrl ?? '',
      apiKey: '',
      enabled: provider?.enabled ?? true,
    },
    RULES,
  );
  const action = useAction(
    (input: ProviderInput) => (provider ? updateProvider(provider.id, input) : createProvider(input)),
    {
      invalidate: [KEYS.providers],
      success: editing ? '已保存' : '已添加厂商',
      failure: '保存没有成功，请检查后重试',
    },
  );
  const submit = async () => {
    const values = form.validate();
    if (values && (await action.run(providerInput(values, editing, clearKey)))) onClose();
  };
  const configured = editing && provider?.apiKeyConfigured;
  return (
    <Dialog
      title={editing ? '修改厂商' : '添加厂商'}
      onClose={onClose}
      onSubmit={() => void submit()}
      footer={<DialogActions okText="保存" loading={action.pending} onCancel={onClose} />}
    >
      <Field label="名称" error={form.errors.name}>
        {(id) => <Input id={id} placeholder="例如：DeepSeek" {...form.text('name')} />}
      </Field>
      <Field
        label="API 地址"
        help="OpenAI 兼容接口的根地址，例如 https://api.deepseek.com/v1"
        error={form.errors.baseUrl}
      >
        {(id) => <Input id={id} placeholder="https://" {...form.text('baseUrl')} />}
      </Field>
      <Field
        label="API Key"
        help={configured ? `已配置（末四位 ${provider.apiKeyLast4}），留空表示不修改` : '保存后不再显示明文'}
        error={form.errors.apiKey}
      >
        {(id) => (
          <Input
            id={id}
            type="password"
            autoComplete="new-password"
            placeholder={editing ? '不修改请留空' : '可选'}
            {...form.text('apiKey')}
          />
        )}
      </Field>
      {configured ? (
        <div className="kw-field">
          <Checkbox label="清除已保存的密钥" checked={clearKey} onCheckedChange={setClearKey} />
        </div>
      ) : null}
      <Switch
        label="启用"
        checked={form.values.enabled}
        onCheckedChange={(checked) => form.set('enabled', checked)}
      />
    </Dialog>
  );
}
