/** 初始化向导：系统首次部署时建首个管理员，完成后跳登录页。已初始化时直接去登录。 */
import { useState, type FormEvent } from 'react';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { Input } from '@openai/apps-sdk-ui/components/Input';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Navigate, useNavigate } from 'react-router';
import {
  fetchSetupStatus,
  initializeSystem,
  MAX_PASSWORD_LENGTH,
  MIN_PASSWORD_LENGTH,
  validateEmail,
  validateNewPassword,
} from '../../api/auth';
import { readableError } from '../../api/http';
import { Field } from '../../ui/Field';
import { rule, useForm, type Rules } from '../../ui/form';
import { Skeleton } from '../../ui/Skeleton';
import { toast } from '../../ui/toastStore';
import { useBrand } from '../brand/queries';
import { AuthCard } from './AuthCard';

interface Values {
  readonly email: string;
  readonly name: string;
  readonly password: string;
  readonly confirm: string;
}

const RULES: Rules<Values> = {
  name: rule.required('请输入姓名'),
  email: rule.all<Values, 'email'>(rule.required('请输入邮箱'), rule.email('邮箱格式不正确')),
  password: (value) =>
    value.length < MIN_PASSWORD_LENGTH ? `密码至少 ${MIN_PASSWORD_LENGTH} 位` : undefined,
  confirm: rule.required('请再输入一次密码'),
};

function validate(values: Values): string | null {
  if (!values.name.trim()) return '请输入姓名';
  return validateEmail(values.email) ?? validateNewPassword(values.password, values.confirm);
}

export default function SetupPage() {
  const brand = useBrand();
  const navigate = useNavigate();
  const client = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const form = useForm<Values>({ email: '', name: '', password: '', confirm: '' }, RULES);
  const status = useQuery({ queryKey: ['setup'], queryFn: fetchSetupStatus, retry: false });

  if (status.isPending) return <Skeleton rows={6} className="kw-page-state" />;
  if (status.data?.initialized) return <Navigate to="/login" replace />;

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    const values = form.validate();
    if (!values) return;
    const problem = validate(values);
    if (problem) {
      setError(problem);
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      await initializeSystem(values);
      client.setQueryData(['setup'], { initialized: true });
      toast.success('初始化完成，请用刚才的账号登录');
      navigate('/login', { replace: true });
    } catch (err) {
      setError(readableError(err, '初始化没有成功，请稍后重试'));
    } finally {
      setSubmitting(false);
    }
  };

  const input = (key: keyof Values, label: string, props: Record<string, unknown>) => (
    <Field label={label} error={form.errors[key]}>
      {(id) => <Input id={id} size="lg" disabled={submitting} {...props} {...form.text(key)} />}
    </Field>
  );

  return (
    <AuthCard
      title={`初始化 ${brand.name}`}
      description="创建第一个系统管理员，之后用这个账号登录并邀请同事"
      error={error ?? (status.isError ? readableError(status.error, '暂时查不到初始化状态') : null)}
    >
      <form onSubmit={(event) => void onSubmit(event)} noValidate>
        {input('name', '姓名', { autoComplete: 'name', maxLength: 200 })}
        {input('email', '邮箱', {
          autoComplete: 'username',
          placeholder: 'name@company.com',
          maxLength: 320,
        })}
        {input('password', '密码', {
          type: 'password',
          autoComplete: 'new-password',
          maxLength: MAX_PASSWORD_LENGTH,
        })}
        {input('confirm', '确认密码', {
          type: 'password',
          autoComplete: 'new-password',
          maxLength: MAX_PASSWORD_LENGTH,
        })}
        <Button type="submit" color="primary" size="lg" block loading={submitting}>
          创建管理员
        </Button>
      </form>
    </AuthCard>
  );
}
