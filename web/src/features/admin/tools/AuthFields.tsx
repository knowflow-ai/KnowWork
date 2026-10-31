/** 登记 MCP 服务时各认证方式自己的字段：OAuth 授权、个人访问令牌、共享凭据的请求头（无需认证没有字段）。 */
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { MinusCircle, Plus } from '@openai/apps-sdk-ui/components/Icon';
import { Input } from '@openai/apps-sdk-ui/components/Input';
import type { HeaderInput, OAuthInput, TokenConfig } from '../../../api/mcp';
import { Field } from '../../../ui/Field';
import { SecretText } from '../../../ui/SecretText';

export function OAuthFields({
  oauth,
  dynamic,
  redirectUri,
  suggestedScope,
  keepSecret,
  onChange,
}: {
  readonly oauth: OAuthInput;
  /** 对方支持自动注册（不知道时按支持算） */
  readonly dynamic: boolean;
  readonly redirectUri: string;
  readonly suggestedScope: string;
  /** 修改时已经有 secret：不填表示沿用 */
  readonly keepSecret: boolean;
  readonly onChange: (oauth: OAuthInput) => void;
}) {
  return (
    <>
      <p className="kw-detail-note">
        {dynamic
          ? '对方支持自动注册：保存时自动在对方那边注册应用，Client ID 可以不填。保存后你会先跳到对方页面授权一次，回来时拉取工具清单。'
          : '对方不支持自动注册：先在对方那边建一个应用，回调地址填下面这个，再把 Client ID 和 Client Secret 填进来。'}
      </p>
      {redirectUri ? (
        <Field label="回调地址">
          <SecretText value={redirectUri} />
        </Field>
      ) : null}
      <div className="kw-field-row">
        <Field label="Client ID" help={dynamic ? '不填就自动注册' : undefined}>
          {(id) => (
            <Input
              id={id}
              maxLength={200}
              value={oauth.clientId}
              onChange={(e) => onChange({ ...oauth, clientId: e.target.value })}
            />
          )}
        </Field>
        <Field label="Client Secret" help={keepSecret ? '不填表示沿用原来的' : '对方的应用没有就不填'}>
          {(id) => (
            <Input
              id={id}
              type="password"
              autoComplete="new-password"
              value={oauth.clientSecret}
              onChange={(e) => onChange({ ...oauth, clientSecret: e.target.value })}
            />
          )}
        </Field>
      </div>
      <Field label="权限范围（scope）" help="不填就用对方建议的；多个用空格隔开">
        {(id) => (
          <Input
            id={id}
            maxLength={500}
            placeholder={suggestedScope}
            value={oauth.scope}
            onChange={(e) => onChange({ ...oauth, scope: e.target.value })}
          />
        )}
      </Field>
    </>
  );
}

export function TokenFields({
  config,
  token,
  editing,
  onConfig,
  onToken,
}: {
  readonly config: TokenConfig;
  readonly token: string;
  readonly editing: boolean;
  readonly onConfig: (config: TokenConfig) => void;
  readonly onToken: (token: string) => void;
}) {
  return (
    <>
      <div className="kw-field-row">
        <Field label="请求头">
          {(id) => (
            <Input
              id={id}
              value={config.header}
              onChange={(e) => onConfig({ ...config, header: e.target.value })}
            />
          )}
        </Field>
        <Field label="值的前缀" help="令牌前面加什么，例如「Bearer 」；不需要就清空">
          {(id) => (
            <Input
              id={id}
              maxLength={32}
              value={config.prefix}
              onChange={(e) => onConfig({ ...config, prefix: e.target.value })}
            />
          )}
        </Field>
      </div>
      <Field label="给成员的说明" help="成员连接时看得到，例如去哪里生成个人令牌、要勾哪些权限">
        {(id) => (
          <Input
            id={id}
            maxLength={300}
            placeholder="例如：在 GitLab 的「偏好设置 → 访问令牌」里生成，勾选 read_api"
            value={config.help}
            onChange={(e) => onConfig({ ...config, help: e.target.value })}
          />
        )}
      </Field>
      <Field
        label="你自己的令牌"
        help={
          editing
            ? '只用来测试连接、拉工具清单，存成你自己的连接；不填就用你已有的连接'
            : '只用来测试连接、拉工具清单，存成你自己的连接，不会给别人用'
        }
      >
        {(id) => (
          <Input
            id={id}
            type="password"
            autoComplete="new-password"
            value={token}
            onChange={(e) => onToken(e.target.value)}
          />
        )}
      </Field>
    </>
  );
}

export function HeaderFields({
  headers,
  onChange,
}: {
  readonly headers: readonly HeaderInput[];
  readonly onChange: (headers: readonly HeaderInput[]) => void;
}) {
  const update = (index: number, patch: Partial<HeaderInput>) =>
    onChange(headers.map((h, i) => (i === index ? { ...h, ...patch } : h)));
  return (
    <div className="kw-header-list">
      {headers.map((header, index) => (
        <div key={index} className="kw-header-row">
          <Input
            placeholder="名字，例如 Authorization"
            aria-label="请求头名字"
            value={header.name}
            onChange={(e) => update(index, { name: e.target.value })}
          />
          <Input
            type="password"
            autoComplete="new-password"
            placeholder="值，例如 Bearer xxxx"
            aria-label="请求头的值"
            value={header.value}
            onChange={(e) => update(index, { value: e.target.value })}
          />
          <Button
            color="secondary"
            variant="ghost"
            uniform
            aria-label="删除这个请求头"
            onClick={() => onChange(headers.filter((_, i) => i !== index))}
          >
            <MinusCircle />
          </Button>
        </div>
      ))}
      <div>
        <Button
          color="secondary"
          variant="outline"
          size="sm"
          onClick={() => onChange([...headers, { name: '', value: '' }])}
        >
          <Plus />
          添加请求头
        </Button>
      </div>
    </div>
  );
}
