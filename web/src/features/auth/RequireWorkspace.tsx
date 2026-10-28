/**
 * 选定当前空间：登录后取「我能进的空间」，上次用的还能进就用它，否则取第一个；一个都进不了时说明原因。
 * 选定之前不渲染页面，各接口的路径都要带当前空间（stores/workspace）。
 */
import { useEffect, type ReactNode } from 'react';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { Lock, Warning } from '@openai/apps-sdk-ui/components/Icon';
import { readableError } from '../../api/http';
import { lastWorkspace, pickWorkspace, useWorkspaceStore } from '../../stores/workspace';
import { Empty } from '../../ui/Empty';
import { Skeleton } from '../../ui/Skeleton';
import { useMyWorkspaces } from './queries';

export function RequireWorkspace({ children }: { readonly children: ReactNode }) {
  const workspaces = useMyWorkspaces();
  const current = useWorkspaceStore((s) => s.id);
  const enter = useWorkspaceStore((s) => s.enter);
  const chosen = workspaces.data ? pickWorkspace(workspaces.data, current ?? lastWorkspace()) : null;

  useEffect(() => {
    if (chosen && chosen !== current) enter(chosen);
  }, [chosen, current, enter]);

  if (workspaces.isPending) return <Skeleton rows={6} className="kw-page-state" />;
  if (workspaces.isError) {
    return (
      <Empty
        area
        icon={<Warning />}
        title="暂时取不到你所在的工作空间"
        description={readableError(workspaces.error, '服务暂时不可用，请稍后重试')}
        action={
          <Button color="primary" onClick={() => void workspaces.refetch()}>
            重试
          </Button>
        }
      />
    );
  }
  if (!chosen) {
    return (
      <Empty area icon={<Lock />} title="你还不在任何工作空间里" description="请联系管理员把你加进工作空间" />
    );
  }
  if (chosen !== current) return <Skeleton rows={6} className="kw-page-state" />;
  return <>{children}</>;
}
