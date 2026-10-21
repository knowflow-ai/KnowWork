/** 登录、注册、初始化共用的居中卡片。报错只写一句原因，不用彩色提示框。 */
import type { ReactNode } from 'react';
import { BrandMark } from '../brand/BrandMark';
import { useBrandHead } from '../brand/queries';

interface Props {
  readonly title: string;
  readonly description: string;
  readonly error?: string | null;
  readonly children: ReactNode;
}

export function AuthCard({ title, description, error, children }: Props) {
  useBrandHead();
  return (
    <main className="kw-login">
      <div className="kw-login-card">
        <BrandMark size={30} className="kw-login-brand" />
        <h1>{title}</h1>
        <p>{description}</p>
        {error ? (
          <div className="kw-login-error" role="alert">
            {error}
          </div>
        ) : null}
        {children}
      </div>
    </main>
  );
}
