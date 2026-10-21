/** 登录页：邮箱 + 密码；后端配了单点登录时多一个企业账号入口；系统还没初始化时转去初始化向导。 */
import { useEffect, useState, type FormEvent } from 'react';
import { Button, ButtonLink } from '@openai/apps-sdk-ui/components/Button';
import { Input } from '@openai/apps-sdk-ui/components/Input';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Navigate, useLocation, useNavigate } from 'react-router';
import {
  fetchSetupStatus,
  login,
  OIDC_LOGIN_URL,
  probeOidc,
  validateLogin,
  type LoginInput,
} from '../../api/auth';
import { readableError } from '../../api/http';
import { useSessionStore } from '../../stores/session';
import { Field } from '../../ui/Field';
import { rule, useForm } from '../../ui/form';
import { Skeleton } from '../../ui/Skeleton';
import { useBrand } from '../brand/queries';
import { AuthCard } from './AuthCard';
import { rememberUser } from './queries';
import { safeRedirect } from './redirect';

const RULES = {
  email: rule.all<LoginInput, 'email'>(rule.required('请输入邮箱'), rule.email('邮箱格式不正确')),
  password: rule.required('请输入密码'),
};

export default function LoginPage() {
  const brand = useBrand();
  const navigate = useNavigate();
  const location = useLocation();
  const client = useQueryClient();
  const resetSession = useSessionStore((s) => s.reset);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const form = useForm<LoginInput>({ email: '', password: '' }, RULES);
  const setup = useQuery({ queryKey: ['setup'], queryFn: fetchSetupStatus, retry: false });
  const oidc = useQuery({
    queryKey: ['oidc-enabled'],
    queryFn: () => probeOidc(),
    staleTime: Infinity,
    retry: false,
  });
  const from = safeRedirect((location.state as { from?: unknown } | null)?.from);

  useEffect(() => resetSession(), [resetSession]);

  if (setup.isPending) return <Skeleton rows={6} className="kw-page-state" />;
  if (setup.data && !setup.data.initialized) return <Navigate to="/setup" replace />;

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    const values = form.validate();
    if (!values) return;
    const problem = validateLogin(values);
    if (problem) {
      setError(problem);
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      rememberUser(client, await login(values));
      navigate(from, { replace: true });
    } catch (err) {
      setError(readableError(err, '登录没有成功，请稍后重试'));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <AuthCard title={`登录 ${brand.name}`} description="使用工作邮箱登录" error={error}>
      <form onSubmit={(event) => void onSubmit(event)} noValidate>
        <Field label="邮箱" error={form.errors.email}>
          {(id) => (
            <Input
              id={id}
              size="lg"
              autoComplete="username"
              placeholder="name@company.com"
              maxLength={320}
              disabled={submitting}
              {...form.text('email')}
            />
          )}
        </Field>
        <Field label="密码" error={form.errors.password}>
          {(id) => (
            <Input
              id={id}
              size="lg"
              type="password"
              autoComplete="current-password"
              maxLength={256}
              disabled={submitting}
              {...form.text('password')}
            />
          )}
        </Field>
        <Button type="submit" color="primary" size="lg" block loading={submitting}>
          登录
        </Button>
      </form>
      {oidc.data ? (
        <>
          <div className="kw-login-divider">或</div>
          <ButtonLink as="a" href={OIDC_LOGIN_URL} color="secondary" variant="outline" size="lg" block>
            使用企业账号登录
          </ButtonLink>
        </>
      ) : null}
    </AuthCard>
  );
}
