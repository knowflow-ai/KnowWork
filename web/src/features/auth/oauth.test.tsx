import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '../../test/user';
import { jsonResponse, ME, renderWithApp, routeFetch } from '../../test/render';
import OAuthConsentPage from './OAuthConsentPage';
import { navigation as leave } from './inClient';
import { clearAfterLogin, peekAfterLogin, rememberAfterLogin } from './afterLogin';

const SEARCH =
  '?response_type=code&client_id=kw-desktop&redirect_uri=http%3A%2F%2F127.0.0.1%3A53111%2Fcallback' +
  '&code_challenge=E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM&code_challenge_method=S256&state=st-1' +
  '&device_name=Alice%20%E7%9A%84%20MacBook';
const BACK = 'http://127.0.0.1:53111/callback?code=kwac_x&state=st-1';

function open() {
  renderWithApp(<OAuthConsentPage />, { route: `/oauth/authorize${SEARCH}`, path: '/oauth/authorize' });
}

beforeEach(() => {
  vi.spyOn(leave, 'go').mockImplementation(() => undefined);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  window.sessionStorage.clear();
});

describe('应用登录确认页', () => {
  it('说明是哪个应用、哪台设备；允许后带着授权码回到应用', async () => {
    const { calls } = routeFetch({
      'GET /api/auth/me': jsonResponse(ME),
      'GET /api/auth/oauth/authorize': jsonResponse({
        client: { id: 'kw-desktop', name: 'KnowWork 桌面端' },
        deviceName: 'Alice 的 MacBook',
      }),
      'POST /api/auth/oauth/authorize': jsonResponse({ redirectTo: BACK }),
    });
    open();
    expect(
      await screen.findByRole('heading', { name: '允许「KnowWork 桌面端」登录你的账号？' }),
    ).toBeInTheDocument();
    expect(screen.getByText(/Alice 的 MacBook/)).toBeInTheDocument();
    expect(screen.getByText('李雷（li@corp.cn）')).toBeInTheDocument();
    const checked = calls.find((c) => c.method === 'GET' && c.path === '/api/auth/oauth/authorize');
    expect(checked?.query.get('client_id')).toBe('kw-desktop');

    await userEvent.click(screen.getByRole('button', { name: '允许' }));
    await waitFor(() => expect(leave.go).toHaveBeenCalledWith(BACK));
    const posted = calls.find((c) => c.method === 'POST')?.body as Record<string, unknown>;
    expect(posted).toMatchObject({ client_id: 'kw-desktop', state: 'st-1', approve: true });
    expect(await screen.findByText('已允许，请回到应用继续')).toBeInTheDocument();
  });

  it('拒绝也回到应用，带着拒绝的结果', async () => {
    const denied = 'http://127.0.0.1:53111/callback?error=access_denied&state=st-1';
    const { calls } = routeFetch({
      'GET /api/auth/oauth/authorize': jsonResponse({
        client: { id: 'kw-desktop', name: 'KnowWork 桌面端' },
        deviceName: '',
      }),
      'POST /api/auth/oauth/authorize': jsonResponse({ redirectTo: denied }),
    });
    open();
    await userEvent.click(await screen.findByRole('button', { name: '拒绝' }));
    await waitFor(() => expect(leave.go).toHaveBeenCalledWith(denied));
    expect(calls.find((c) => c.method === 'POST')?.body).toMatchObject({ approve: false });
  });

  it('请求有问题、但能带回应用时，自动回到应用', async () => {
    const back = 'http://127.0.0.1:53111/callback?error=invalid_request&state=st-1';
    routeFetch({
      'GET /api/auth/oauth/authorize': jsonResponse({
        error: { code: 'invalid_request', message: 'PKCE 只支持 S256', redirectTo: back },
      }),
    });
    open();
    await waitFor(() => expect(leave.go).toHaveBeenCalledWith(back));
    expect(screen.queryByRole('button', { name: '允许' })).not.toBeInTheDocument();
  });

  it('应用或回调地址不可信时只在页面上说明，不跳走', async () => {
    routeFetch({
      'GET /api/auth/oauth/authorize': jsonResponse({
        error: { code: 'invalid_client', message: '未登记的客户端' },
      }),
    });
    open();
    expect(await screen.findByText('这个登录请求无效：未登记的客户端')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '允许' })).not.toBeInTheDocument();
    expect(leave.go).not.toHaveBeenCalled();
  });
});

describe('单点登录回来后回到原来的页面', () => {
  it('只记站内地址，清掉后就没有了', () => {
    rememberAfterLogin('/oauth/authorize?client_id=kw-desktop');
    expect(peekAfterLogin()).toBe('/oauth/authorize?client_id=kw-desktop');
    clearAfterLogin();
    expect(peekAfterLogin()).toBeNull();
    rememberAfterLogin('https://evil.example/');
    expect(peekAfterLogin()).toBeNull();
    rememberAfterLogin('/chat');
    expect(peekAfterLogin()).toBeNull();
  });
});
