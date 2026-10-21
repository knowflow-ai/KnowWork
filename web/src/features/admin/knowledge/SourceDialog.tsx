/** 新建或修改知识源（KnowFlow）。密钥只能写不能读：修改时留空表示不改；换了认证方式要重新填。 */
import { Input } from '@openai/apps-sdk-ui/components/Input';
import { RadioGroup } from '@openai/apps-sdk-ui/components/RadioGroup';
import { Switch } from '@openai/apps-sdk-ui/components/Switch';
import { TagInput } from '@openai/apps-sdk-ui/components/TagInput';
import {
  AUTH_MODES,
  createSource,
  MIN_ACCOUNT_KEY_LENGTH,
  MIN_SERVICE_KEY_LENGTH,
  updateSource,
  validateSourceInput,
  type AuthMode,
  type KnowledgeSource,
  type SecretStatus,
  type SourceInput,
  type StoredSecret,
} from '../../../api/knowledge';
import { Dialog, DialogActions } from '../../../ui/Dialog';
import { Field } from '../../../ui/Field';
import { rule, useForm, type Rules } from '../../../ui/form';
import { NumberInput } from '../../../ui/NumberInput';
import { toast } from '../../../ui/toastStore';
import { useAction } from '../useAction';
import { initialValues, toSourceInput, type Values } from './sourceForm';
import { KEYS } from '../queries';

const MODE_HELP: Readonly<Record<AuthMode, string>> = {
  shared: '所有人都用下面这一个 KnowFlow 账号访问；谁能查哪个知识库只看 KnowWork 的知识库授权',
  assertion: '要 KnowFlow 支持签名断言才能用，目前还不支持；请改成统一账号',
};

const RULES: Rules<Values> = {
  name: rule.all<Values, 'name'>(rule.required('请输入名称'), rule.maxLength(100, '名称不能超过 100 个字')),
  baseUrl: rule.required('请输入地址'),
};

/** 修改时这条知识源现在的认证方式与它的密钥状态。 */
function storedSecret(source?: KnowledgeSource): StoredSecret | undefined {
  const detail = source?.detail;
  if (!detail) return undefined;
  const mode = detail.config.authMode;
  const status = mode === 'shared' ? detail.accountKey : detail.serviceKey;
  return { authMode: mode, configured: status.configured };
}

function keyHelp(status: SecretStatus | undefined, sameMode: boolean, required: string): string {
  return status?.configured && sameMode ? `已配置（末四位 ${status.last4}），留空表示不修改` : required;
}

export function SourceDialog({
  source,
  onClose,
}: {
  readonly source?: KnowledgeSource;
  readonly onClose: () => void;
}) {
  const editing = source !== undefined;
  const stored = storedSecret(source);
  const form = useForm<Values>(initialValues(source), RULES);
  const { values, errors, set } = form;
  const mode = values.authMode;
  // 签名断言先隐藏：只有还在用它的知识源才给出选择，方便改成统一账号
  const modes =
    stored?.authMode === 'assertion' ? AUTH_MODES : AUTH_MODES.filter((m) => m.value === 'shared');
  const sameMode = stored?.authMode === mode;
  const action = useAction(
    (input: SourceInput) => (source ? updateSource(source.id, input) : createSource(input)),
    {
      invalidate: [KEYS.sources, KEYS.knowledgeBases],
      success: editing ? '已保存' : '已添加知识源',
      failure: '保存没有成功，请检查后重试',
    },
  );
  const submit = async () => {
    const checked = form.validate();
    if (!checked) return;
    const input = toSourceInput(checked);
    const problem = validateSourceInput(input, stored);
    if (problem) {
      toast.warning(problem);
      return;
    }
    if (await action.run(input)) onClose();
  };
  const secret = (key: 'serviceKey' | 'accountKey', label: string, required: string) => (
    <Field label={label} help={keyHelp(source?.detail?.[key], sameMode, required)}>
      {(id) => (
        <Input
          id={id}
          type="password"
          autoComplete="new-password"
          placeholder={sameMode ? '不修改请留空' : ''}
          {...form.text(key)}
        />
      )}
    </Field>
  );
  return (
    <Dialog
      title={editing ? '修改知识源' : '添加知识源'}
      size="md"
      onClose={onClose}
      onSubmit={() => void submit()}
      footer={<DialogActions okText="保存" loading={action.pending} onCancel={onClose} />}
    >
      <Field label="名称" error={errors.name}>
        {(id) => <Input id={id} placeholder="例如：公司制度库" {...form.text('name')} />}
      </Field>
      <Field label="KnowFlow 地址" error={errors.baseUrl}>
        {(id) => <Input id={id} placeholder="https://knowflow.example.com" {...form.text('baseUrl')} />}
      </Field>
      <Field label="认证方式" help={MODE_HELP[mode]}>
        <RadioGroup<AuthMode>
          aria-label="认证方式"
          direction="row"
          value={mode}
          onChange={(next) => set('authMode', next)}
        >
          {modes.map((m) => (
            <RadioGroup.Item key={m.value} value={m.value}>
              {m.label}
            </RadioGroup.Item>
          ))}
        </RadioGroup>
      </Field>
      {mode === 'assertion'
        ? secret('serviceKey', '服务密钥', `必填，至少 ${MIN_SERVICE_KEY_LENGTH} 个字符，保存后不再显示`)
        : null}
      {mode === 'shared'
        ? secret(
            'accountKey',
            'KnowFlow API Key',
            `必填，至少 ${MIN_ACCOUNT_KEY_LENGTH} 个字符，保存后不再显示。建议专门建一个持有这些知识库的 KnowFlow 账号，用它的 Key`,
          )
        : null}
      <Field
        label="开放的知识库 ID"
        help={
          mode === 'shared'
            ? '留空表示这个账号在 KnowFlow 读得到的全部知识库（含它以后新建的）；也可以只填其中几个。谁能查哪个，再到「资源授权」里按知识库授权'
            : '留空表示调用者在 KnowFlow 能读的全部知识库；也可以只填其中几个'
        }
      >
        {(id) => (
          <TagInput
            id={id}
            size="md"
            placeholder="留空表示全部；指定时输入 ID 后回车"
            value={values.datasetIds.map((value) => ({ value, valid: true }))}
            onChange={(tags) =>
              set(
                'datasetIds',
                tags.map((tag) => tag.value),
              )
            }
          />
        )}
      </Field>
      <Field label="打开原文的地址" help="留空则使用 KnowFlow 地址">
        {(id) => <Input id={id} placeholder="https://" {...form.text('webBaseUrl')} />}
      </Field>
      <div className="kw-field-row kw-field-row-three">
        <Field label="相似度阈值">
          {(id) => (
            <NumberInput
              id={id}
              decimal
              min={0}
              max={1}
              value={values.similarityThreshold}
              onChange={(n) => set('similarityThreshold', n ?? 0)}
            />
          )}
        </Field>
        <Field label="向量权重">
          {(id) => (
            <NumberInput
              id={id}
              decimal
              min={0}
              max={1}
              value={values.vectorSimilarityWeight}
              onChange={(n) => set('vectorSimilarityWeight', n ?? 0)}
            />
          )}
        </Field>
        <Field label="超时（秒）">
          {(id) => (
            <NumberInput
              id={id}
              min={1}
              max={60}
              value={values.timeoutSeconds}
              onChange={(n) => set('timeoutSeconds', n ?? 15)}
            />
          )}
        </Field>
      </div>
      {editing ? (
        <Switch
          label="启用"
          checked={values.enabled}
          onCheckedChange={(checked) => set('enabled', checked)}
        />
      ) : null}
    </Dialog>
  );
}
