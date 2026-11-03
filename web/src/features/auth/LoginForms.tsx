/** 登录页的两种账号密码表单：工作邮箱，与域账号（LDAP / AD 等，由登录方式列表给出提交地址）。 */
import { useState, type FormEvent } from 'react';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { Input } from '@openai/apps-sdk-ui/components/Input';
import {
  directoryLogin,
  login,
  validateLogin,
  type CurrentUser,
  type DirectoryLoginInput,
  type LoginInput,
  type LoginMethod,
} from '../../api/auth';
import { readableError } from '../../api/http';
import { Field } from '../../ui/Field';
import { rule, useForm } from '../../ui/form';

const EMAIL_RULES = {
  email: rule.all<LoginInput, 'email'>(rule.required('请输入邮箱'), rule.email('邮箱格式不正确')),
  password: rule.required('请输入密码'),
};
const DIRECTORY_RULES = {
  username: rule.required('请输入账号'),
  password: rule.required('请输入密码'),
};

interface FormProps {
  readonly onDone: (user: CurrentUser) => void;
  readonly onError: (message: string | null) => void;
}

function useSubmit(onDone: FormProps['onDone'], onError: FormProps['onError']) {
  const [submitting, setSubmitting] = useState(false);
  const run = async (attempt: () => Promise<CurrentUser>) => {
    onError(null);
    setSubmitting(true);
    try {
      onDone(await attempt());
    } catch (err) {
      onError(readableError(err, '登录没有成功，请稍后重试'));
    } finally {
      setSubmitting(false);
    }
  };
  return { submitting, run };
}

function PasswordField({
  error,
  disabled,
  props,
}: {
  readonly error?: string;
  readonly disabled: boolean;
  readonly props: object;
}) {
  return (
    <Field label="密码" error={error}>
      {(id) => (
        <Input
          id={id}
          size="lg"
          type="password"
          autoComplete="current-password"
          maxLength={256}
          disabled={disabled}
          {...props}
        />
      )}
    </Field>
  );
}

export function EmailLoginForm({ onDone, onError }: FormProps) {
  const form = useForm<LoginInput>({ email: '', password: '' }, EMAIL_RULES);
  const { submitting, run } = useSubmit(onDone, onError);
  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    const values = form.validate();
    if (!values) return;
    const problem = validateLogin(values);
    if (problem) {
      onError(problem);
      return;
    }
    await run(() => login(values));
  };
  return (
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
      <PasswordField error={form.errors.password} disabled={submitting} props={form.text('password')} />
      <Button type="submit" color="primary" size="lg" block loading={submitting}>
        登录
      </Button>
    </form>
  );
}

export function DirectoryLoginForm({
  method,
  onDone,
  onError,
}: FormProps & { readonly method: LoginMethod }) {
  const form = useForm<DirectoryLoginInput>({ username: '', password: '' }, DIRECTORY_RULES);
  const { submitting, run } = useSubmit(onDone, onError);
  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    const values = form.validate();
    if (values) await run(() => directoryLogin(method.url, values));
  };
  return (
    <form onSubmit={(event) => void onSubmit(event)} noValidate>
      <Field label="账号" help="域账号，如 zhangsan，或 zhangsan@corp.com" error={form.errors.username}>
        {(id) => (
          <Input
            id={id}
            size="lg"
            autoComplete="username"
            maxLength={256}
            disabled={submitting}
            {...form.text('username')}
          />
        )}
      </Field>
      <PasswordField error={form.errors.password} disabled={submitting} props={form.text('password')} />
      <Button type="submit" color="primary" size="lg" block loading={submitting}>
        登录
      </Button>
    </form>
  );
}
