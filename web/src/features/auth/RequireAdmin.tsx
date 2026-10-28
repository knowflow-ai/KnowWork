import { type ReactNode } from 'react';
import { Lock } from '@openai/apps-sdk-ui/components/Icon';
import { Empty } from '../../ui/Empty';
import { Skeleton } from '../../ui/Skeleton';
import { useIsWorkspaceAdmin } from './queries';

/** 管理页：只有空间 owner/admin 能进；其他人看到说明。服务端仍会逐个接口判定。 */
export function RequireAdmin({ children }: { readonly children: ReactNode }) {
  const admin = useIsWorkspaceAdmin();
  if (admin.isPending) return <Skeleton rows={6} className="kw-page-state" />;
  if (!admin.data) {
    return (
      <Empty area icon={<Lock />} title="没有权限" description="只有工作空间的所有者或管理员能打开管理页面" />
    );
  }
  return <>{children}</>;
}
