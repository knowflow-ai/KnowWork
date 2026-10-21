/** 邀请注册：/invite?token=…，填姓名与密码，注册后直接登录进入对话页。 */
import { useState, type FormEvent } from 'react';
import { Button, ButtonLink } from '@openai/apps-sdk-ui/components/Button';
import { Warning } from '@openai/apps-sdk-ui/components/Icon';
import { Input } from '@openai/apps-sdk-ui/components/Input';
import { useQueryClient } from '@tanstack/react-query';
import { useNavigate, useSearchParams } from 'react-router';
import {
  acceptInvitation,
  isPlausibleInviteToken,
  MAX_PASSWORD_LENGTH,
  MIN_PASSWORD_LENGTH,
  validateNewPassword,
} from '../../api/auth';
import { readableError } from '../../api/http';
import { Empty } from '../../ui/Empty';
import { Field } from '../../ui/Field';
import { rule, useForm, type Rules } from '../../ui/form';
import { useBrand } from '../brand/queries';
import { AuthCard } from './AuthCard';
import { rememberUser } from './queries';

interface Values {
  readonly name: string;
  readonly password: string;
  readonly confirm: string;
}

const RULES: Rules<Values> = {
  name: rule.maxLength(200, '姓名不能超过 200 个字'),
  password: (value) =>
    value.length < MIN_PASSWORD_LENGTH ? `密码至少 ${MIN_PASSWORD_LENGTH} 位` : undefined,
  confirm: rule.required('请再输入一次密码'),
};

export default function InvitePage() {
  const brand = useBrand();
  const [params] = useSearchParams();
  const token = params.get('token');
  const navigate = useNavigate();
  const client = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const form = useForm<Values>({ name: '', password: '', confirm: '' }, RULES);

  if (!isPlausibleInviteToken(token)) {
    return (
      <Empty
        className="kw-page-state"
        icon={<Warning />}
        title="邀请链接无效"
        description="链接不完整或已被修改，请向管理员重新索取邀请链接"
        action={
          <ButtonLink as="a" href="/login" color="secondary" variant="outline">
            去登录
          </ButtonLink>
        }
      />
    );
  }

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    const values = form.validate();
    if (!values) return;
    const problem = validateNewPassword(values.password, values.confirm);
    if (problem) {
      setError(problem);
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      rememberUser(client, await acceptInvitation({ token, name: values.name, password: values.password }));
      navigate('/chat', { replace: true });
    } catch (err) {
      setError(readableError(err, '注册没有成功，请稍后重试'));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <AuthCard title={`加入 ${brand.name}`} description="设置你的姓名与登录密码" error={error}>
      <form onSubmit={(event) => void onSubmit(event)} noValidate>
        <Field label="姓名" error={form.errors.name}>
          {(id) => (
            <Input
              id={id}
              size="lg"
              autoComplete="name"
              placeholder="同事怎么称呼你"
              maxLength={200}
              disabled={submitting}
              {...form.text('name')}
            />
          )}
        </Field>
        <Field label="密码" error={form.errors.password}>
          {(id) => (
            <Input
              id={id}
              size="lg"
              type="password"
              autoComplete="new-password"
              maxLength={MAX_PASSWORD_LENGTH}
              disabled={submitting}
              {...form.text('password')}
            />
          )}
        </Field>
        <Field label="确认密码" error={form.errors.confirm}>
          {(id) => (
            <Input
              id={id}
              size="lg"
              type="password"
              autoComplete="new-password"
              maxLength={MAX_PASSWORD_LENGTH}
              disabled={submitting}
              {...form.text('confirm')}
            />
          )}
        </Field>
        <Button type="submit" color="primary" size="lg" block loading={submitting}>
          注册并登录
        </Button>
      </form>
    </AuthCard>
  );
}
