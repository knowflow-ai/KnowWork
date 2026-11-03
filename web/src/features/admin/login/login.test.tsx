import { afterEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '../../../test/user';
import { jsonResponse, ME, renderWithApp, routeFetch } from '../../../test/render';
import { validateFieldPath, validateProviderUrl } from '../../../api/loginProviders';
import LoginMethodsPage from './LoginMethodsPage';

afterEach(() => vi.unstubAllGlobals());

const BASE = '/api/system/login-providers';
const ENV = {
  id: 'oidc',
  kind: 'oidc',
  name: '企业账号',
  enabled: true,
  signup: 'known_only',
  config: { issuer: 'https://idp.corp.cn', client_id: 'kw' },
  has_secret: true,
  secret_last4: 'cret',
  callback_url: 'https://kw.corp.cn/api/auth/oidc/callback',
  readonly: true,
};
const CORP = {
  id: 'lp_1',
  kind: 'oauth2',
  name: '集团统一认证',
  enabled: true,
  signup: 'auto',
  config: {
    authorize_url: 'https://sso.corp.cn/oauth/authorize',
    token_url: 'https://sso.corp.cn/oauth/token',
    userinfo_url: 'https://sso.corp.cn/api/me',
    client_id: 'kw-client',
    scopes: ['profile'],
    token_auth: 'basic',
    pkce: true,
    userinfo_token: 'header',
    subject_path: 'data.user.id',
    name_path: 'data.user.name',
    email_path: 'data.user.mail',
    email_trusted: false,
  },
  has_secret: true,
  secret_last4: '1234',
  callback_url: 'https://kw.corp.cn/api/auth/sso/lp_1/callback',
  readonly: false,
};

function api(providers: readonly object[] = [ENV, CORP], allowed = true, extra = {}) {
  return routeFetch({
    'GET /api/auth/me': jsonResponse(ME),
    'GET /api/system/check': jsonResponse({ allowed }),
    [`GET ${BASE}`]: jsonResponse({ providers }),
    ...extra,
  });
}

async function type(dialog: HTMLElement, label: string, value: string) {
  const input = within(dialog).getByLabelText(label);
  await userEvent.clear(input);
  await userEvent.type(input, value);
}

describe('登录方式', () => {
  it('列出登录方式：环境变量配的只能看，后台配的能改能删', async () => {
    api();
    renderWithApp(<LoginMethodsPage />);
    const envRow = (await screen.findByText('企业账号')).closest('tr') as HTMLElement;
    expect(within(envRow).getByText('OIDC · 环境变量')).toBeInTheDocument();
    expect(within(envRow).getByText('改环境变量')).toBeInTheDocument();
    const corpRow = screen.getByText('集团统一认证').closest('tr') as HTMLElement;
    expect(within(corpRow).getByText('OAuth2')).toBeInTheDocument();
    expect(within(corpRow).getByText('首次登录自动建号')).toBeInTheDocument();
    expect(within(corpRow).getByRole('button', { name: '修改' })).toBeInTheDocument();
  });

  it('添加 OAuth2：设置按接口的下划线字段发出，保存后换成修改并给出回调地址', async () => {
    const calls = api([], true, { [`POST ${BASE}`]: jsonResponse(CORP, 201) });
    renderWithApp(<LoginMethodsPage />);
    await userEvent.click(await screen.findByRole('button', { name: '添加登录方式' }));
    const dialog = await screen.findByRole('dialog', { name: '添加登录方式' });
    await userEvent.click(within(dialog).getByRole('radio', { name: 'OAuth2' }));
    await type(dialog, '名称', '集团统一认证');
    await type(dialog, '授权地址', 'https://sso.corp.cn/oauth/authorize');
    await type(dialog, '令牌地址', 'https://sso.corp.cn/oauth/token');
    await type(dialog, '用户信息地址', 'https://sso.corp.cn/api/me');
    await type(dialog, '客户端 ID', 'kw-client');
    await type(dialog, '客户端密钥', 'corp-secret-1234');
    await type(dialog, 'ID 的字段路径', 'data.user.id');
    await userEvent.click(within(dialog).getByRole('radio', { name: '首次登录自动建号' }));
    await userEvent.click(within(dialog).getByRole('button', { name: '保存' }));
    await waitFor(() => expect(calls.find('POST', BASE)).toHaveLength(1));
    expect(calls.find('POST', BASE)[0]?.body).toEqual({
      kind: 'oauth2',
      name: '集团统一认证',
      enabled: true,
      signup: 'auto',
      client_secret: 'corp-secret-1234',
      config: {
        authorize_url: 'https://sso.corp.cn/oauth/authorize',
        token_url: 'https://sso.corp.cn/oauth/token',
        userinfo_url: 'https://sso.corp.cn/api/me',
        client_id: 'kw-client',
        scopes: [],
        token_auth: 'basic',
        pkce: true,
        userinfo_token: 'header',
        subject_path: 'data.user.id',
        name_path: 'name',
        email_path: 'email',
        email_trusted: false,
      },
    });
    const edit = await screen.findByRole('dialog', { name: '修改登录方式' });
    expect(within(edit).getByText(CORP.callback_url)).toBeInTheDocument();
  });

  it('地址不是 https、字段路径写错时不提交', async () => {
    const calls = api([]);
    renderWithApp(<LoginMethodsPage />);
    await userEvent.click(await screen.findByRole('button', { name: '添加登录方式' }));
    const dialog = await screen.findByRole('dialog', { name: '添加登录方式' });
    await type(dialog, '名称', '身份中台');
    await type(dialog, '发行方地址（Issuer）', 'http://idp.corp.cn');
    await type(dialog, '客户端 ID', 'kw');
    await type(dialog, '客户端密钥', 's3cret-1234');
    await userEvent.click(within(dialog).getByRole('button', { name: '保存' }));
    expect(await within(dialog).findByText('地址必须是 https（本机地址除外）')).toBeInTheDocument();
    expect(calls.find('POST', BASE)).toHaveLength(0);
  });

  it('修改时密钥留空就不发，沿用原密钥', async () => {
    const calls = api([CORP], true, {
      [`PATCH ${BASE}/lp_1`]: jsonResponse({ ...CORP, name: '统一认证' }),
    });
    renderWithApp(<LoginMethodsPage />);
    const row = (await screen.findByText('集团统一认证')).closest('tr') as HTMLElement;
    await userEvent.click(within(row).getByRole('button', { name: '修改' }));
    const dialog = await screen.findByRole('dialog', { name: '修改登录方式' });
    expect(within(dialog).getByText(CORP.callback_url)).toBeInTheDocument();
    expect(within(dialog).getByText(/末四位 1234/)).toBeInTheDocument();
    await type(dialog, '名称', '统一认证');
    await userEvent.click(within(dialog).getByRole('button', { name: '保存' }));
    await waitFor(() => expect(calls.find('PATCH', `${BASE}/lp_1`)).toHaveLength(1));
    const body = calls.find('PATCH', `${BASE}/lp_1`)[0]?.body as Record<string, unknown>;
    expect(body.name).toBe('统一认证');
    expect(body).not.toHaveProperty('client_secret');
    expect(body.config).toMatchObject({ subject_path: 'data.user.id', scopes: ['profile'] });
  });

  it('不是系统管理员时说明没有权限', async () => {
    api([], false);
    renderWithApp(<LoginMethodsPage />);
    expect(await screen.findByText('没有权限')).toBeInTheDocument();
  });
});

describe('登录方式的校验', () => {
  it('地址：https，或本机的 http；不带账号密码', () => {
    expect(validateProviderUrl('https://sso.corp.cn/oauth')).toBeNull();
    expect(validateProviderUrl('http://localhost:9000/x')).toBeNull();
    expect(validateProviderUrl('http://sso.corp.cn')).toBe('地址必须是 https（本机地址除外）');
    expect(validateProviderUrl('https://u:p@sso.corp.cn')).toBe('地址里不能带账号密码或 #');
    expect(validateProviderUrl('不是地址')).toBe('地址格式不正确');
  });

  it('字段路径：点分写法', () => {
    expect(validateFieldPath('data.user.0.id')).toBeNull();
    expect(validateFieldPath('data..id')).not.toBeNull();
    expect(validateFieldPath('')).toBe('请输入字段路径');
  });
});
