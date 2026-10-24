/** 新建或修改模型：所属厂商、调用别名、上游模型名、能力、对话模型的思考、上下文长度与单价。 */
import { Checkbox } from '@openai/apps-sdk-ui/components/Checkbox';
import { Input } from '@openai/apps-sdk-ui/components/Input';
import { Select } from '@openai/apps-sdk-ui/components/Select';
import { Switch } from '@openai/apps-sdk-ui/components/Switch';
import {
  ALIAS_PATTERN,
  CAPABILITIES,
  createModel,
  updateModel,
  type Model,
  type ModelInput,
  type Provider,
  type Thinking,
} from '../../../api/models';
import { Dialog, DialogActions } from '../../../ui/Dialog';
import { Field } from '../../../ui/Field';
import { rule, useForm, type Rules } from '../../../ui/form';
import { NumberInput } from '../../../ui/NumberInput';
import { useAction } from '../useAction';
import { KEYS } from '../queries';

interface Props {
  readonly model?: Model;
  readonly providers: readonly Provider[];
  readonly onClose: () => void;
}

// 下拉框只认字符串：跟随服务默认存成 null
const THINKING_OPTIONS = [
  { value: 'default', label: '跟随服务默认' },
  { value: 'on', label: '开启' },
  { value: 'off', label: '关闭' },
];
const thinkingFrom = (value: string): Thinking => (value === 'on' || value === 'off' ? value : null);

const RULES: Rules<ModelInput> = {
  providerId: rule.required('请选择厂商'),
  alias: (value) => (ALIAS_PATTERN.test(value) ? undefined : '只能用字母、数字与 _ . : -，最长 64 个字符'),
  name: rule.all<ModelInput, 'name'>(
    rule.required('请输入厂商那边的模型名'),
    rule.maxLength(200, '模型名过长'),
  ),
  capabilities: rule.required('至少选一项能力'),
};

export function ModelDialog({ model, providers, onClose }: Props) {
  const editing = model !== undefined;
  const form = useForm<ModelInput>(
    {
      providerId: model?.providerId ?? providers[0]?.id ?? '',
      alias: model?.alias ?? '',
      name: model?.name ?? '',
      capabilities: [...(model?.capabilities ?? ['chat'])],
      contextLength: model?.contextLength ?? null,
      thinking: model?.thinking ?? null,
      inputPrice: model?.inputPrice ?? null,
      outputPrice: model?.outputPrice ?? null,
      enabled: model?.enabled ?? true,
    },
    RULES,
  );
  const action = useAction(
    (input: ModelInput) => (model ? updateModel(model.id, input) : createModel(input)),
    {
      invalidate: [KEYS.models],
      success: editing ? '已保存' : '已添加模型',
      failure: '保存没有成功，请检查后重试',
    },
  );
  const submit = async () => {
    const values = form.validate();
    if (values && (await action.run(values))) onClose();
  };
  const { values, errors, set } = form;
  const toggle = (capability: string, checked: boolean) =>
    set(
      'capabilities',
      checked ? [...values.capabilities, capability] : values.capabilities.filter((c) => c !== capability),
    );
  return (
    <Dialog
      title={editing ? '修改模型' : '添加模型'}
      size="md"
      onClose={onClose}
      onSubmit={() => void submit()}
      footer={<DialogActions okText="保存" loading={action.pending} onCancel={onClose} />}
    >
      <Field label="厂商" error={errors.providerId}>
        <Select
          block
          disabled={editing}
          options={providers.map((p) => ({ value: p.id, label: p.name }))}
          value={values.providerId}
          placeholder="先添加厂商"
          onChange={(option) => set('providerId', option.value)}
        />
      </Field>
      <Field label="别名" help="技能与对话里引用模型时用的名字" error={errors.alias}>
        {(id) => <Input id={id} placeholder="例如 chat-default" {...form.text('alias')} />}
      </Field>
      <Field label="上游模型名" error={errors.name}>
        {(id) => <Input id={id} placeholder="例如 deepseek-chat" {...form.text('name')} />}
      </Field>
      <Field label="能力" error={errors.capabilities}>
        <div className="kw-check-row">
          {CAPABILITIES.map((c) => (
            <Checkbox
              key={c.value}
              label={c.label}
              checked={values.capabilities.includes(c.value)}
              onCheckedChange={(checked) => toggle(c.value, checked)}
            />
          ))}
        </div>
      </Field>
      {values.capabilities.includes('chat') ? (
        <Field
          label="思考"
          help="Qwen3 这类模型在自建服务上默认开着思考：每一步先想一段再动手，回答要多等几秒。关掉更快，复杂问题可能答得没那么周全。按服务商的写法下发，认不出的服务按 vLLM、SGLang 处理"
        >
          <Select
            block
            options={THINKING_OPTIONS}
            value={values.thinking ?? 'default'}
            onChange={(option) => set('thinking', thinkingFrom(option.value))}
          />
        </Field>
      ) : null}
      <Field
        label="上下文长度（token）"
        help="嵌入模型填它的最大输入长度：知识库按它定分块大小，超出的部分模型会直接截掉"
      >
        {(id) => (
          <NumberInput
            id={id}
            min={1}
            max={10_000_000}
            value={values.contextLength}
            onChange={(n) => set('contextLength', n)}
          />
        )}
      </Field>
      <div className="kw-field-row">
        <Field label="输入单价（元 / 百万 token）">
          {(id) => (
            <NumberInput
              id={id}
              decimal
              min={0}
              max={99_999_999}
              value={values.inputPrice}
              onChange={(n) => set('inputPrice', n)}
            />
          )}
        </Field>
        <Field label="输出单价（元 / 百万 token）">
          {(id) => (
            <NumberInput
              id={id}
              decimal
              min={0}
              max={99_999_999}
              value={values.outputPrice}
              onChange={(n) => set('outputPrice', n)}
            />
          )}
        </Field>
      </div>
      <Switch label="启用" checked={values.enabled} onCheckedChange={(checked) => set('enabled', checked)} />
    </Dialog>
  );
}
