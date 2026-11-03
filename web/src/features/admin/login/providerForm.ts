/** 登录方式表单：界面上的值、从已有配置填回、转成接口要的设置（下划线字段）。 */
import {
  configStrings,
  validateFieldPath,
  validateProviderUrl,
  type LoginProvider,
  type LoginProviderInput,
  type LoginProviderKind,
  type Signup,
} from '../../../api/loginProviders';
import { rule, type Rules } from '../../../ui/form';

export interface Values {
  readonly name: string;
  readonly enabled: boolean;
  readonly signup: Signup;
  readonly clientId: string;
  readonly clientSecret: string;
  readonly scopes: readonly string[];
  // OIDC
  readonly issuer: string;
  readonly trustUnverifiedEmail: boolean;
  // OAuth2
  readonly authorizeUrl: string;
  readonly tokenUrl: string;
  readonly userinfoUrl: string;
  readonly tokenAuth: 'basic' | 'post';
  readonly pkce: boolean;
  readonly userinfoToken: 'header' | 'query';
  readonly subjectPath: string;
  readonly namePath: string;
  readonly emailPath: string;
  readonly emailTrusted: boolean;
}

export const DEFAULT_SCOPES: Record<LoginProviderKind, readonly string[]> = {
  oidc: ['openid', 'email', 'profile'],
  oauth2: [],
};

const textOf = (config: Readonly<Record<string, unknown>>, key: string, fallback = '') =>
  typeof config[key] === 'string' ? (config[key] as string) : fallback;

export function initialValues(kind: LoginProviderKind, provider?: LoginProvider): Values {
  const config = provider?.config ?? {};
  return {
    name: provider?.name ?? '',
    enabled: provider?.enabled ?? true,
    signup: provider?.signup ?? 'known_only',
    clientId: textOf(config, 'client_id'),
    clientSecret: '',
    scopes: provider ? configStrings(config, 'scopes') : DEFAULT_SCOPES[kind],
    issuer: textOf(config, 'issuer'),
    trustUnverifiedEmail: config.trust_unverified_email === true,
    authorizeUrl: textOf(config, 'authorize_url'),
    tokenUrl: textOf(config, 'token_url'),
    userinfoUrl: textOf(config, 'userinfo_url'),
    tokenAuth: config.token_auth === 'post' ? 'post' : 'basic',
    pkce: config.pkce !== false,
    userinfoToken: config.userinfo_token === 'query' ? 'query' : 'header',
    subjectPath: textOf(config, 'subject_path', 'id'),
    namePath: textOf(config, 'name_path', 'name'),
    emailPath: textOf(config, 'email_path', 'email'),
    emailTrusted: config.email_trusted === true,
  };
}

const url = (value: string) => validateProviderUrl(value) ?? undefined;
const path = (value: string) => validateFieldPath(value) ?? undefined;

export function rulesFor(kind: LoginProviderKind, editing: boolean): Rules<Values> {
  const common: Rules<Values> = {
    name: rule.all<Values, 'name'>(rule.required('请输入名称'), rule.maxLength(40, '名称不能超过 40 个字')),
    clientId: rule.all<Values, 'clientId'>(
      rule.required('请输入客户端 ID'),
      rule.maxLength(255, '客户端 ID 过长'),
    ),
    // 修改时留空表示不改密钥
    clientSecret: editing ? rule.maxLength(2048, '密钥过长') : rule.required('请输入客户端密钥'),
  };
  if (kind === 'oidc') return { ...common, issuer: url };
  return {
    ...common,
    authorizeUrl: url,
    tokenUrl: url,
    userinfoUrl: url,
    subjectPath: path,
    namePath: path,
    emailPath: path,
  };
}

function algorithmsOf(provider?: LoginProvider): readonly string[] {
  const kept = provider ? configStrings(provider.config, 'algorithms') : [];
  return kept.length > 0 ? kept : ['RS256'];
}

export function toInput(
  kind: LoginProviderKind,
  values: Values,
  provider?: LoginProvider,
): LoginProviderInput {
  const config =
    kind === 'oidc'
      ? {
          issuer: values.issuer.trim(),
          client_id: values.clientId.trim(),
          scopes: values.scopes,
          // 界面上不改签名算法：沿用已有的，新建默认 RS256（服务端只收非对称算法）
          algorithms: algorithmsOf(provider),
          trust_unverified_email: values.trustUnverifiedEmail,
        }
      : {
          authorize_url: values.authorizeUrl.trim(),
          token_url: values.tokenUrl.trim(),
          userinfo_url: values.userinfoUrl.trim(),
          client_id: values.clientId.trim(),
          scopes: values.scopes,
          token_auth: values.tokenAuth,
          pkce: values.pkce,
          userinfo_token: values.userinfoToken,
          subject_path: values.subjectPath.trim(),
          name_path: values.namePath.trim(),
          email_path: values.emailPath.trim(),
          email_trusted: values.emailTrusted,
        };
  return {
    name: values.name,
    enabled: values.enabled,
    signup: values.signup,
    config,
    clientSecret: values.clientSecret || undefined,
  };
}
