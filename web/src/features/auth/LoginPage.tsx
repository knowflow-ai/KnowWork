/**
 * 登录页：工作邮箱，开了域账号登录时多一个「域账号」的切换（LDAP / AD 等，按服务端给的登录方式列表）；
 * 下面列单点登录、企业微信等跳转类入口。在企业微信等客户端里打开时直接走它的免登（inClient.ts）。
 * 系统还没初始化时转去初始化向导。
 */
import { useEffect, useState } from 'react';
import { ButtonLink } from '@openai/apps-sdk-ui/components/Button';
import { SegmentedControl } from '@openai/apps-sdk-ui/components/SegmentedControl';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Navigate, useLocation, useNavigate } from 'react-router';
import { fetchLoginMethods, fetchSetupStatus, type CurrentUser } from '../../api/auth';
import { useSessionStore } from '../../stores/session';
import { Skeleton } from '../../ui/Skeleton';
import { useBrand } from '../brand/queries';
import { AuthCard } from './AuthCard';
import { inClientTarget, navigation } from './inClient';
import { DirectoryLoginForm, EmailLoginForm } from './LoginForms';
import { rememberUser } from './queries';
import { safeRedirect } from './redirect';

const EMAIL = 'email';

export default function LoginPage() {
  const brand = useBrand();
  const navigate = useNavigate();
  const location = useLocation();
  const client = useQueryClient();
  const resetSession = useSessionStore((s) => s.reset);
  const [error, setError] = useState<string | null>(null);
  const [picked, setPicked] = useState<string | null>(null);
  const setup = useQuery({ queryKey: ['setup'], queryFn: fetchSetupStatus, retry: false });
  const methods = useQuery({
    queryKey: ['login-methods'],
    queryFn: fetchLoginMethods,
    staleTime: Infinity,
    retry: false,
  });
  const all = methods.data?.methods ?? [];
  const redirects = all.filter((m) => m.style === 'redirect');
  const directories = all.filter((m) => m.style === 'password');
  const emailOn = methods.data?.password !== false;
  const from = safeRedirect((location.state as { from?: unknown } | null)?.from);

  useEffect(() => resetSession(), [resetSession]);
  useEffect(() => {
    if (!methods.data) return;
    const target = inClientTarget(methods.data.methods, navigator.userAgent, location.search);
    if (target) navigation.go(target);
  }, [methods.data, location.search]);

  if (setup.isPending) return <Skeleton rows={6} className="kw-page-state" />;
  if (setup.data && !setup.data.initialized) return <Navigate to="/setup" replace />;

  // 默认工作邮箱；关了邮箱登录时默认第一个域账号
  const current = picked ?? (emailOn || directories.length === 0 ? EMAIL : directories[0]!.id);
  const directory = directories.find((m) => m.id === current);
  const choose = (value: string) => {
    setPicked(value);
    setError(null);
  };
  const done = (user: CurrentUser) => {
    rememberUser(client, user);
    navigate(from, { replace: true });
  };
  const choices = [...(emailOn ? [{ id: EMAIL, label: '工作邮箱' }] : []), ...directories];

  return (
    <AuthCard
      title={`登录 ${brand.name}`}
      description={directory ? `使用${directory.label}登录` : '使用工作邮箱登录'}
      error={error}
    >
      {choices.length > 1 ? (
        <div className="kw-login-switch">
          <SegmentedControl<string> aria-label="登录方式" value={current} onChange={choose} block>
            {choices.map((choice) => (
              <SegmentedControl.Option key={choice.id} value={choice.id}>
                {choice.label}
              </SegmentedControl.Option>
            ))}
          </SegmentedControl>
        </div>
      ) : null}
      {directory ? (
        <DirectoryLoginForm key={directory.id} method={directory} onDone={done} onError={setError} />
      ) : (
        <EmailLoginForm onDone={done} onError={setError} />
      )}
      {redirects.length > 0 ? (
        <>
          <div className="kw-login-divider">或</div>
          <div className="kw-login-methods">
            {redirects.map((method) => (
              <ButtonLink
                key={method.id}
                as="a"
                href={method.url}
                color="secondary"
                variant="outline"
                size="lg"
                block
              >
                使用{method.label}登录
              </ButtonLink>
            ))}
          </div>
        </>
      ) : null}
    </AuthCard>
  );
}
