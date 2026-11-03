/**
 * 新建或修改登录方式（OIDC、通用 OAuth2）。密钥只能写不能读：修改时留空表示不改。
 * 新建保存后弹窗留着、换成修改，把回调地址给管理员抄去对方登记（登记好才能登录）。
 */
import { useState } from 'react';
import { Input } from '@openai/apps-sdk-ui/components/Input';
import { SegmentedControl } from '@openai/apps-sdk-ui/components/SegmentedControl';
import { Switch } from '@openai/apps-sdk-ui/components/Switch';
import {
  createLoginProvider,
  updateLoginProvider,
  type LoginProvider,
  type LoginProviderKind,
  type Signup,
} from '../../../api/loginProviders';
import { Dialog, DialogActions } from '../../../ui/Dialog';
import { Field } from '../../../ui/Field';
import { useForm } from '../../../ui/form';
import { SecretText } from '../../../ui/SecretText';
import { TAG_DELIMITERS, TagInput } from '../../../ui/TagInput';
import { useAction } from '../useAction';
import { DEFAULT_SCOPES, initialValues, rulesFor, toInput, type Values } from './providerForm';
import { LOGIN_PROVIDERS_KEY } from './queries';

const SIGNUP_HELP: Record<Signup, string> = {
  known_only: '只有已经在系统里的人（含被邀请的）能用它登录；其他人提示联系管理员开通',
  auto: '第一次用它登录的人自动建账号，进默认空间',
};

type Form = ReturnType<typeof useForm<Values>>;

/** 开关加一句说明（开关自己没有说明文字的位置）。 */
function SwitchField({
  label,
  checked,
  onChange,
  note,
}: {
  readonly label: string;
  readonly checked: boolean;
  readonly onChange: (checked: boolean) => void;
  readonly note?: string;
}) {
  return (
    <div className="kw-field">
      <Switch label={label} checked={checked} onCheckedChange={onChange} />
      {note ? <div className="kw-field-note">{note}</div> : null}
    </div>
  );
}

function TextField({
  form,
  name,
  label,
  help,
  placeholder,
  type,
}: {
  readonly form: Form;
  readonly name: keyof Values;
  readonly label: string;
  readonly help?: string;
  readonly placeholder?: string;
  readonly type?: 'password';
}) {
  return (
    <Field label={label} help={help} error={form.errors[name]}>
      {(id) => (
        <Input
          id={id}
          type={type}
          autoComplete={type === 'password' ? 'new-password' : 'off'}
          placeholder={placeholder}
          {...form.text(name)}
        />
      )}
    </Field>
  );
}

function ScopeField({ form, kind }: { readonly form: Form; readonly kind: LoginProviderKind }) {
  return (
    <Field label="Scope" help={kind === 'oidc' ? '要包含 openid' : '按对方文档填，不需要可以留空'}>
      {(id) => (
        <TagInput
          id={id}
          size="md"
          placeholder={DEFAULT_SCOPES[kind].join(' ') || '例如 profile'}
          delimiters={[...TAG_DELIMITERS, ' ']}
          value={form.values.scopes.map((value) => ({ value, valid: true }))}
          onChange={(tags) =>
            form.set(
              'scopes',
              tags.map((tag) => tag.value),
            )
          }
        />
      )}
    </Field>
  );
}

function SecretField({ form, provider }: { readonly form: Form; readonly provider?: LoginProvider }) {
  const kept = provider?.hasSecret;
  return (
    <TextField
      form={form}
      name="clientSecret"
      label="客户端密钥"
      type="password"
      placeholder={kept ? '不修改请留空' : undefined}
      help={kept ? `已配置（末四位 ${provider.secretLast4 || '—'}），留空表示不修改` : '保存后不再显示明文'}
    />
  );
}

function OidcFields({ form, provider }: { readonly form: Form; readonly provider?: LoginProvider }) {
  return (
    <>
      <TextField
        form={form}
        name="issuer"
        label="发行方地址（Issuer）"
        placeholder="https://idp.example.com/realms/corp"
        help="和对方发现文档里的 issuer 一字不差"
      />
      <TextField form={form} name="clientId" label="客户端 ID" />
      <SecretField form={form} provider={provider} />
      <ScopeField form={form} kind="oidc" />
      <SwitchField
        label="信任未验证的邮箱"
        checked={form.values.trustUnverifiedEmail}
        onChange={(checked) => form.set('trustUnverifiedEmail', checked)}
        note="有的企业身份中台不给「邮箱已验证」；确认邮箱都由企业统一分配后再打开，否则第一次登录时不按邮箱对人"
      />
    </>
  );
}

function OAuth2Fields({ form, provider }: { readonly form: Form; readonly provider?: LoginProvider }) {
  return (
    <>
      <TextField
        form={form}
        name="authorizeUrl"
        label="授权地址"
        placeholder="https://sso.example.com/oauth/authorize"
      />
      <TextField
        form={form}
        name="tokenUrl"
        label="令牌地址"
        placeholder="https://sso.example.com/oauth/token"
      />
      <TextField
        form={form}
        name="userinfoUrl"
        label="用户信息地址"
        placeholder="https://sso.example.com/api/me"
      />
      <TextField form={form} name="clientId" label="客户端 ID" />
      <SecretField form={form} provider={provider} />
      <ScopeField form={form} kind="oauth2" />
      <Field label="客户端凭证放在哪" help="换令牌时怎么带客户端 ID 与密钥，按对方文档选">
        <SegmentedControl<'basic' | 'post'>
          aria-label="客户端凭证放在哪"
          value={form.values.tokenAuth}
          onChange={(value) => form.set('tokenAuth', value)}
        >
          <SegmentedControl.Option value="basic">Basic 请求头</SegmentedControl.Option>
          <SegmentedControl.Option value="post">表单字段</SegmentedControl.Option>
        </SegmentedControl>
      </Field>
      <Field label="访问令牌怎么传" help="调用户信息接口时令牌放在哪">
        <SegmentedControl<'header' | 'query'>
          aria-label="访问令牌怎么传"
          value={form.values.userinfoToken}
          onChange={(value) => form.set('userinfoToken', value)}
        >
          <SegmentedControl.Option value="header">Bearer 请求头</SegmentedControl.Option>
          <SegmentedControl.Option value="query">查询参数 access_token</SegmentedControl.Option>
        </SegmentedControl>
      </Field>
      <SwitchField
        label="使用 PKCE"
        checked={form.values.pkce}
        onChange={(checked) => form.set('pkce', checked)}
        note="对方不支持 PKCE 时关掉"
      />
      <TextField
        form={form}
        name="subjectPath"
        label="ID 的字段路径"
        placeholder="id"
        help="用户信息响应里取值的位置，用点分开，例如 data.user.id；ID 每个人唯一、不会变"
      />
      <TextField form={form} name="namePath" label="姓名的字段路径" placeholder="name" />
      <TextField form={form} name="emailPath" label="邮箱的字段路径" placeholder="email" />
      <SwitchField
        label="邮箱可信"
        checked={form.values.emailTrusted}
        onChange={(checked) => form.set('emailTrusted', checked)}
        note="OAuth2 不说明邮箱有没有验证过；确认邮箱都由企业统一分配后再打开，打开后第一次登录按邮箱对上已有账号"
      />
    </>
  );
}

export function LoginProviderDialog({
  provider,
  onClose,
  onCreated,
}: {
  readonly provider?: LoginProvider;
  readonly onClose: () => void;
  /** 新建成功：父组件换成修改这条，好让管理员看到回调地址。 */
  readonly onCreated: (created: LoginProvider) => void;
}) {
  const editing = provider !== undefined;
  const [kind, setKind] = useState<LoginProviderKind>(provider?.kind ?? 'oidc');
  const form = useForm<Values>(initialValues(kind, provider), rulesFor(kind, editing));
  const action = useAction(
    async (values: Values) => {
      const input = toInput(kind, values, provider);
      if (provider) return updateLoginProvider(provider.id, input);
      onCreated(await createLoginProvider(kind, input));
      return undefined;
    },
    {
      invalidate: [LOGIN_PROVIDERS_KEY],
      success: editing ? '已保存' : '已添加，把回调地址登记到对方后就能登录',
      failure: '保存没有成功，请检查后重试',
    },
  );
  const submit = async () => {
    const values = form.validate();
    if (values && (await action.run(values)) && editing) onClose();
  };
  const chooseKind = (next: LoginProviderKind) => {
    setKind(next);
    form.set('scopes', DEFAULT_SCOPES[next]);
  };
  return (
    <Dialog
      title={editing ? '修改登录方式' : '添加登录方式'}
      size="md"
      onClose={onClose}
      onSubmit={() => void submit()}
      footer={<DialogActions okText="保存" loading={action.pending} onCancel={onClose} />}
    >
      {editing ? (
        <Field label="回调地址" help="登记到对方（身份中台、统一认证）的回调地址，也叫重定向 URI">
          <SecretText value={provider.callbackUrl} />
        </Field>
      ) : (
        <Field
          label="类型"
          help={
            kind === 'oidc'
              ? '标准的单点登录：Keycloak、Azure AD、Okta、Authing 等身份中台'
              : '没有 OIDC、要拿访问令牌调对方用户信息接口的统一认证'
          }
        >
          <SegmentedControl<LoginProviderKind> aria-label="类型" value={kind} onChange={chooseKind}>
            <SegmentedControl.Option value="oidc">OIDC</SegmentedControl.Option>
            <SegmentedControl.Option value="oauth2">OAuth2</SegmentedControl.Option>
          </SegmentedControl>
        </Field>
      )}
      <TextField
        form={form}
        name="name"
        label="名称"
        placeholder="例如：集团统一认证"
        help="登录页的按钮写「使用{名称}登录」"
      />
      {kind === 'oidc' ? (
        <OidcFields form={form} provider={provider} />
      ) : (
        <OAuth2Fields form={form} provider={provider} />
      )}
      <Field label="谁能登录" help={SIGNUP_HELP[form.values.signup]}>
        <SegmentedControl<Signup>
          aria-label="谁能登录"
          value={form.values.signup}
          onChange={(value) => form.set('signup', value)}
        >
          <SegmentedControl.Option value="known_only">只允许已有账号</SegmentedControl.Option>
          <SegmentedControl.Option value="auto">首次登录自动建号</SegmentedControl.Option>
        </SegmentedControl>
      </Field>
      <SwitchField
        label="启用"
        checked={form.values.enabled}
        onChange={(checked) => form.set('enabled', checked)}
      />
    </Dialog>
  );
}
