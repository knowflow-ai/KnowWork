/** 受保护页面：先取当前用户；未登录或会话过期跳登录页，取不到时给出可重试的说明；登录了再选定当前空间。 */
import { type ReactNode } from 'react';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { Warning } from '@openai/apps-sdk-ui/components/Icon';
import { Navigate, useLocation } from 'react-router';
import { readableError } from '../../api/http';
import { useSessionStore } from '../../stores/session';
import { Empty } from '../../ui/Empty';
import { Skeleton } from '../../ui/Skeleton';
import { useMe } from './queries';
import { RequireWorkspace } from './RequireWorkspace';

export function RequireAuth({ children }: { readonly children: ReactNode }) {
  const me = useMe();
  const expired = useSessionStore((s) => s.expired);
  const location = useLocation();
  const toLogin = <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;

  if (expired) return toLogin;
  if (me.isPending) return <Skeleton rows={6} className="kw-page-state" />;
  if (me.isError) {
    return (
      <Empty
        area
        icon={<Warning />}
        title="暂时无法确认登录状态"
        description={readableError(me.error, '服务暂时不可用，请稍后重试')}
        action={
          <Button color="primary" onClick={() => void me.refetch()}>
            重试
          </Button>
        }
      />
    );
  }
  if (!me.data) return toLogin;
  return <RequireWorkspace>{children}</RequireWorkspace>;
}
