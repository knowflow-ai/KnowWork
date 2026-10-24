/**
 * 运行面板（系统设置，只有系统管理员能进）：后台任务各池的排队与执行、下游并发、worker 心跳，下面是任务列表。
 * 只显示任务 id、类型、对象 id、阶段与错误码，不显示文件内容和错误详情；服务端同样逐个接口判定。
 */
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { useQueryClient } from '@tanstack/react-query';
import { Empty } from '../../../ui/Empty';
import { Skeleton } from '../../../ui/Skeleton';
import { useIsSystemAdmin } from '../../brand/queries';
import { AdminPage } from '../AdminPage';
import { JobList } from './JobList';
import { GateTable, PoolTable, WorkerTable } from './OverviewTables';
import { JOBS_KEY, useJobsOverview } from './queries';

function Overview() {
  const overview = useJobsOverview();
  const client = useQueryClient();
  if (overview.isPending) return <Skeleton rows={6} />;
  if (overview.isError) {
    return <Empty title="运行情况加载失败" description="请稍后刷新重试" />;
  }
  const { pools, gates, workers, kinds } = overview.data;
  return (
    <>
      <section className="kw-jobs-section" aria-label="任务池">
        <div className="kw-jobs-head">
          <h2>任务池</h2>
          <Button
            color="secondary"
            variant="outline"
            size="sm"
            onClick={() => void client.invalidateQueries({ queryKey: JOBS_KEY })}
          >
            刷新
          </Button>
        </div>
        <PoolTable pools={pools} />
      </section>
      <section className="kw-jobs-section" aria-label="下游并发">
        <h2>下游并发</h2>
        <GateTable gates={gates} />
      </section>
      <section className="kw-jobs-section" aria-label="Worker">
        <h2>Worker</h2>
        <WorkerTable workers={workers} />
      </section>
      <section className="kw-jobs-section" aria-label="任务">
        <h2>任务</h2>
        <JobList kinds={kinds} pools={pools.map((pool) => pool.pool)} />
      </section>
    </>
  );
}

export default function JobsPage() {
  const systemAdmin = useIsSystemAdmin();
  if (systemAdmin.isPending) return <Skeleton rows={6} className="kw-page-state" />;
  if (!systemAdmin.data) {
    return (
      <Empty
        className="kw-page-state"
        title="没有权限"
        description="只有系统管理员（初始化时创建的账号）能查看运行面板"
      />
    );
  }
  return (
    <AdminPage
      title="运行面板"
      description="后台任务（文档解析、富化、同步、导出导入）的排队与执行情况，每 10 秒自动刷新。取消的任务按处理失败收尾。"
    >
      <Overview />
    </AdminPage>
  );
}
