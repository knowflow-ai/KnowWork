/**
 * 添加或修改 MCP 服务。保存时服务端会先连一次、拉工具清单，连不上就不保存（提示原因）。
 * 认证方式四选一（业内的叫法，两类：共享凭据与用户凭据）：
 * - 无需认证：对方不要凭据（例如公开的文档检索），所有人直接用；
 * - 共享凭据：管理员配一份 API Key 或账号的请求头，所有人共用这份凭据和它的权限；
 * - 个人访问令牌：成员在「设置 → 连接」里填自己的，登记人自己也要填一份，只用来测试连接、拉工具清单；
 * - OAuth 授权：成员在「设置 → 连接」里跳到对方页面授权；登记时自动在对方那边注册应用（不支持时管理员填 Client ID），
 *   保存后登记人马上跳去授权一次，回来时拉第一份工具清单。
 * 无需认证与共享凭据在服务端都是 shared，区别只是有没有请求头。
 * 填完地址会先探一次：不要凭据就默认无需认证（对方另外支持 OAuth 时只提示可以改）；要凭据时声明了 OAuth 默认 OAuth 授权，
 * 否则默认个人访问令牌。请求头、令牌、Client Secret 都只能写不能读。
 */
import { useRef, useState, type ReactNode } from 'react';
import { Checkbox } from '@openai/apps-sdk-ui/components/Checkbox';
import { Input } from '@openai/apps-sdk-ui/components/Input';
import { SegmentedControl } from '@openai/apps-sdk-ui/components/SegmentedControl';
import {
  DEFAULT_TOKEN_CONFIG,
  validateServerInput,
  type HeaderInput,
  type McpServer,
  type OAuthInput,
  type Probe,
  type ServerAuth,
  type ServerInput,
  type TokenConfig,
} from '../../../api/mcp';
import { Dialog, DialogActions } from '../../../ui/Dialog';
import { Field } from '../../../ui/Field';
import { rule, useForm, type Rules } from '../../../ui/form';
import { NumberInput } from '../../../ui/NumberInput';
import { toast } from '../../../ui/toastStore';
import { useAction } from '../useAction';
import { HeaderFields, OAuthFields, TokenFields } from './AuthFields';
import { useMcpLogin } from './login';
import { useMcpScope } from './scope';

type Auth = 'none' | ServerAuth;
const toServerAuth = (auth: Auth): ServerAuth => (auth === 'none' ? 'shared' : auth);
const choiceOf = (server: McpServer): Auth =>
  server.auth === 'shared' && server.headers.length === 0 ? 'none' : server.auth;
const EMPTY_OAUTH: OAuthInput = { clientId: '', clientSecret: '', scope: '' };

interface Values {
  readonly title: string;
  readonly name: string;
  readonly url: string;
  readonly timeoutSeconds: number;
  readonly headers: readonly HeaderInput[];
  readonly auth: Auth;
  readonly tokenConfig: TokenConfig;
  readonly token: string;
  readonly oauth: OAuthInput;
}

/** 照着另一个服务新登记（收为企业 MCP）：地址、认证方式等照抄，凭据留空让管理员重填（请求头只带名字）。 */
function fromTemplate(template: McpServer): Values {
  const base = initialValues(template);
  return {
    ...base,
    headers: template.headers.length
      ? template.headers.map((h) => ({ name: h.name, value: '' }))
      : [{ name: 'Authorization', value: '' }],
  };
}

function initialValues(server?: McpServer): Values {
  if (!server)
    return {
      title: '',
      name: '',
      url: '',
      timeoutSeconds: 60,
      headers: [{ name: 'Authorization', value: '' }],
      auth: 'user_token',
      tokenConfig: DEFAULT_TOKEN_CONFIG,
      token: '',
      oauth: EMPTY_OAUTH,
    };
  return {
    title: server.title,
    name: server.name,
    url: server.url,
    timeoutSeconds: server.timeoutSeconds,
    headers: [],
    auth: choiceOf(server),
    tokenConfig: server.tokenConfig ?? DEFAULT_TOKEN_CONFIG,
    token: '',
    oauth: {
      clientId: server.oauth && !server.oauth.dynamic ? server.oauth.clientId : '',
      clientSecret: '',
      scope: server.oauth?.scope ?? '',
    },
  };
}

const AUTH_HELP: Record<Auth, string> = {
  none: '对方不要凭据（例如公开的文档检索），所有人直接用',
  shared: '管理员配一份 API Key 或账号的请求头，所有人共用这份凭据和它的权限',
  user_token: '每个成员在「设置 → 连接」里填自己的个人访问令牌，按本人的权限调用',
  oauth: '每个成员在「设置 → 连接」里跳到对方页面授权，按本人的权限调用',
};
const OPTIONAL_OAUTH =
  '对方不要凭据也能用，也支持 OAuth 授权：要按人区分（例如用各自的额度、看到各自的内容）时改选「OAuth 授权」';

const sameConfig = (a: TokenConfig | null, b: TokenConfig) =>
  a !== null && a.header === b.header && a.prefix === b.prefix && a.help === b.help;

function sameOAuth(server: McpServer, oauth: OAuthInput) {
  const current = initialValues(server).oauth;
  return (
    current.clientId === oauth.clientId.trim() &&
    current.scope === oauth.scope.trim() &&
    !oauth.clientSecret.trim()
  );
}

function wanted(values: Values): Partial<ServerInput> {
  if (values.auth === 'none' || values.auth === 'shared') return { auth: 'shared' };
  if (values.auth === 'oauth') return { auth: 'oauth', oauth: values.oauth };
  return { auth: 'user_token', tokenConfig: values.tokenConfig, token: values.token };
}

/** 修改时只有认证相关的设置变了（或填了新令牌）才带上，免得只改名称也要重新连接或重新授权。 */
function authChanges(server: McpServer | undefined, values: Values): Partial<ServerInput> {
  if (server === undefined) return wanted(values);
  const changed =
    toServerAuth(values.auth) !== server.auth ||
    (values.auth === 'user_token' &&
      (!sameConfig(server.tokenConfig, values.tokenConfig) || values.token.trim() !== '')) ||
    (values.auth === 'oauth' && !sameOAuth(server, values.oauth));
  return changed ? wanted(values) : {};
}

/** 要发的请求头：无需认证时清空（原来有的话），共享凭据且勾了重新设置时整组替换，其余不改。 */
function headersToSend(
  values: Values,
  server: McpServer | undefined,
  resetHeaders: boolean,
): readonly HeaderInput[] | undefined {
  if (values.auth === 'none') return server && server.headers.length === 0 ? undefined : [];
  if (values.auth === 'shared' && resetHeaders)
    return values.headers.filter((h) => h.name.trim() || h.value.trim());
  return undefined;
}

export function ServerDialog({
  server,
  template,
  onCreate,
  title,
  intro,
  onClose,
}: {
  readonly server?: McpServer;
  /** 照着这个服务新登记（预填，凭据留空）；给了 onCreate 时用它代替普通的登记接口 */
  readonly template?: McpServer;
  readonly onCreate?: (input: ServerInput) => Promise<McpServer | undefined>;
  readonly title?: string;
  /** 表单上面的一段说明 */
  readonly intro?: ReactNode;
  readonly onClose: () => void;
}) {
  const editing = server !== undefined;
  const rules: Rules<Values> = {
    title: rule.required('请输入名称'),
    url: rule.required('请输入地址'),
    ...(editing ? {} : { name: rule.required('请输入标识') }),
  };
  const form = useForm<Values>(template ? fromTemplate(template) : initialValues(server), rules);
  const [resetHeaders, setResetHeaders] = useState(!editing);
  const [probe, setProbe] = useState<Probe>();
  const authTouched = useRef(editing || template !== undefined); // 照着别的服务建时认证方式已定，探测不改它
  const saved = useRef<McpServer>(undefined);
  const [, login] = useMcpLogin();
  const scope = useMcpScope();
  const oauthNow = form.values.auth === 'oauth';
  const action = useAction(
    async (input: ServerInput) => {
      saved.current = server
        ? await scope.api.update(server.id, input)
        : await (onCreate ?? scope.api.create)(input);
    },
    {
      invalidate: [scope.key],
      success: editing ? '已保存' : oauthNow ? '已添加，正在跳到对方页面授权' : '已添加，工具清单已拉取',
      failure: '保存没有成功',
    },
  );
  /** 填完地址探一次定默认的认证方式（管理员自己选过就不动）。探不到不提示，按原来的选。 */
  const detect = async () => {
    const url = form.values.url.trim();
    if (editing || !/^https?:\/\/\S+$/.test(url)) return;
    try {
      const found = await scope.api.probe(url);
      setProbe(found);
      if (authTouched.current) return;
      form.set('auth', !found.needsCredentials ? 'none' : found.oauth ? 'oauth' : 'user_token');
    } catch {
      setProbe(undefined);
    }
  };
  const chooseAuth = (auth: Auth) => {
    authTouched.current = true;
    form.set('auth', auth);
  };
  const submit = async () => {
    const values = form.validate();
    if (!values) return;
    const headers = headersToSend(values, server, resetHeaders);
    const input: ServerInput = {
      name: server?.name ?? values.name.trim(),
      title: values.title,
      url: values.url,
      timeoutSeconds: values.timeoutSeconds,
      headers,
      ...authChanges(server, values),
    };
    const problem = validateServerInput(input, !editing);
    if (problem) {
      toast.warning(problem);
      return;
    }
    if (!(await action.run(input))) return;
    onClose();
    // 新登记的 OAuth 授权服务，或刚改成 OAuth 授权：登记人马上去授权一次，回来时拉工具清单
    const result = saved.current;
    if (result?.auth === 'oauth' && (!editing || server.auth !== 'oauth'))
      void login(result.id, scope.returnTo);
  };
  const { errors } = form;
  return (
    <Dialog
      title={title ?? (editing ? '修改 MCP 服务' : '添加 MCP 服务')}
      size="md"
      onClose={onClose}
      onSubmit={() => void submit()}
      footer={
        <DialogActions
          okText={action.pending ? '正在连接' : '保存'}
          loading={action.pending}
          onCancel={onClose}
        />
      }
    >
      {intro}
      <Field label="名称" error={errors.title}>
        {(id) => <Input id={id} placeholder="例如：GitHub" maxLength={100} {...form.text('title')} />}
      </Field>
      <Field
        label="标识"
        help={
          editing
            ? '工具名前缀，建好后不能修改'
            : '用作工具名前缀 mcp__标识__，建好后不能修改。小写字母开头，只含小写字母、数字和连字符'
        }
        error={errors.name}
      >
        {(id) => (
          <Input id={id} placeholder="例如 github" maxLength={24} disabled={editing} {...form.text('name')} />
        )}
      </Field>
      <Field label="地址" help="MCP 服务的 Streamable HTTP 端点，一般以 /mcp 结尾" error={errors.url}>
        {(id) => (
          <Input
            id={id}
            placeholder="https://mcp.example.com/mcp"
            {...form.text('url')}
            onBlur={() => void detect()}
          />
        )}
      </Field>
      <Field
        label="认证方式"
        help={form.values.auth === 'none' && probe?.oauth ? OPTIONAL_OAUTH : AUTH_HELP[form.values.auth]}
      >
        <SegmentedControl<Auth> aria-label="认证方式" value={form.values.auth} onChange={chooseAuth}>
          <SegmentedControl.Option value="none">无需认证</SegmentedControl.Option>
          <SegmentedControl.Option value="shared">共享凭据</SegmentedControl.Option>
          <SegmentedControl.Option value="user_token">个人访问令牌</SegmentedControl.Option>
          <SegmentedControl.Option value="oauth">OAuth 授权</SegmentedControl.Option>
        </SegmentedControl>
      </Field>
      {oauthNow ? (
        <OAuthFields
          oauth={form.values.oauth}
          dynamic={probe ? probe.dynamicRegistration : (server?.oauth?.dynamic ?? true)}
          redirectUri={probe?.redirectUri || server?.oauth?.redirectUri || ''}
          suggestedScope={probe?.scope || server?.oauth?.suggestedScope || ''}
          keepSecret={Boolean(server?.oauth?.hasSecret)}
          onChange={(oauth) => form.set('oauth', oauth)}
        />
      ) : form.values.auth === 'user_token' ? (
        <TokenFields
          config={form.values.tokenConfig}
          token={form.values.token}
          editing={editing}
          onConfig={(config) => form.set('tokenConfig', config)}
          onToken={(token) => form.set('token', token)}
        />
      ) : form.values.auth === 'shared' ? (
        <Field label="请求头" help="API Key 或账号凭据放在这里，加密保存，保存后不再显示">
          <>
            {editing ? (
              <div className="kw-header-current">
                <span className="kw-secondary-text">
                  {server.headers.length
                    ? server.headers.map((h) => `${h.name}（末四位 ${h.last4 || '****'}）`).join('、')
                    : '没有配置请求头'}
                </span>
                <Checkbox label="重新设置请求头" checked={resetHeaders} onCheckedChange={setResetHeaders} />
              </div>
            ) : null}
            {resetHeaders ? (
              <HeaderFields
                headers={form.values.headers}
                onChange={(headers) => form.set('headers', headers)}
              />
            ) : null}
          </>
        </Field>
      ) : null}
      <Field label="单次调用超时（秒）">
        {(id) => (
          <NumberInput
            id={id}
            className="kw-input-narrow"
            min={5}
            max={300}
            value={form.values.timeoutSeconds}
            onChange={(n) => form.set('timeoutSeconds', n ?? 60)}
          />
        )}
      </Field>
    </Dialog>
  );
}
