/**
 * 技能详情里的「最近运行」：上面按版本汇总（成功率、主要失败阶段、平均耗时），下面是最近的每一次运行，
 * 点开看那一轮的每一步（复用运行记录的运行详情）。只有空间管理员能看，服务端也会再判。
 */
import { lazy, Suspense, useState } from 'react';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { workspaceRun } from '../../../api/debug';
import { readableError } from '../../../api/http';
import type { SkillRunRow, SkillRunStatus, SkillVersionStats } from '../../../api/skillRuns';
import { memberLabel } from '../../../api/workspace';
import { formatDateTime } from '../../../utils/format';
import { FilterSelect } from '../../../ui/FilterSelect';
import { Skeleton } from '../../../ui/Skeleton';
import { StatusDot } from '../../../ui/StatusDot';
import { Table, type Column } from '../../../ui/Table';
import { duration } from '../../debug/format';
import { Toolbar } from '../AdminPage';
import { useMembers } from '../queries';
import { useSkillRunSummary, useSkillRuns } from './queries';
import { modeLabel, reasonLabel, RUN_STATUS, stageLabel, successRate } from './runsModel';

const RunInspector = lazy(() => import('../../debug/RunInspector'));

const RANGES = [
  { value: '7', label: '最近 7 天' },
  { value: '30', label: '最近 30 天' },
  { value: '90', label: '最近 90 天' },
] as const;

const STATUSES = (Object.keys(RUN_STATUS) as SkillRunStatus[]).map((value) => ({
  value,
  label: RUN_STATUS[value].text,
}));

const VERSION_COLUMNS: readonly Column<SkillVersionStats>[] = [
  {
    key: 'version',
    title: '版本',
    width: 110,
    render: (row) => <span className="kw-num">{row.version}</span>,
  },
  { key: 'runs', title: '运行', width: 70, align: 'right', render: (row) => row.runs },
  { key: 'rate', title: '成功率', width: 80, align: 'right', render: (row) => successRate(row) },
  { key: 'failed', title: '失败', width: 70, align: 'right', render: (row) => row.failed },
  { key: 'stage', title: '主要失败在', width: 110, render: (row) => stageLabel(row.topFailureStage) },
  { key: 'duration', title: '平均耗时', width: 100, render: (row) => duration(row.avgDurationMs) },
  { key: 'tokens', title: '平均 Token', width: 100, align: 'right', render: (row) => row.avgTokens },
  {
    key: 'last',
    title: '最近一次',
    width: 150,
    render: (row) => <span className="kw-num">{formatDateTime(row.lastRunAt)}</span>,
  },
];

function Failure({ row }: { readonly row: SkillRunRow }) {
  if (row.status !== 'failed') return <span className="kw-secondary">—</span>;
  const reason = reasonLabel(row.failureStage, row.failureCode);
  return (
    <span className="kw-cell-stack">
      <span>{stageLabel(row.failureStage)}</span>
      {reason ? <span className="kw-secondary">{reason}</span> : null}
    </span>
  );
}

function runColumns(memberName: (userId: string) => string): readonly Column<SkillRunRow>[] {
  return [
    {
      key: 'started',
      title: '开始时间',
      width: 150,
      render: (row) => <span className="kw-num">{formatDateTime(row.startedAt)}</span>,
    },
    { key: 'user', title: '成员', ellipsis: true, render: (row) => memberName(row.userId) },
    {
      key: 'version',
      title: '版本',
      width: 90,
      render: (row) => <span className="kw-num">{row.version}</span>,
    },
    { key: 'mode', title: '方式', width: 100, render: (row) => modeLabel(row.selectionMode) },
    {
      key: 'status',
      title: '状态',
      width: 90,
      render: (row) => (
        <StatusDot tone={RUN_STATUS[row.status].tone}>{RUN_STATUS[row.status].text}</StatusDot>
      ),
    },
    { key: 'failure', title: '失败在', width: 170, render: (row) => <Failure row={row} /> },
    {
      key: 'steps',
      title: '工具调用',
      width: 100,
      render: (row) => (
        <span className="kw-cell-stack">
          <span>{row.steps} 次</span>
          {row.failedSteps > 0 ? <span className="kw-secondary">{row.failedSteps} 次出错</span> : null}
        </span>
      ),
    },
    { key: 'duration', title: '耗时', width: 90, render: (row) => duration(row.durationMs) },
  ];
}

export function SkillRuns({ skillId }: { readonly skillId: string }) {
  const [days, setDays] = useState('30');
  const [status, setStatus] = useState<SkillRunStatus>();
  const [version, setVersion] = useState<string>();
  const [opened, setOpened] = useState<SkillRunRow>();
  const members = useMembers();
  const summary = useSkillRunSummary(skillId, days);
  const runs = useSkillRuns(skillId, { days, status, version });
  const rows = (runs.data?.pages ?? []).flatMap((page) => page.items);
  const memberName = (userId: string) => {
    const member = members.data?.find((m) => m.userId === userId);
    return member ? memberLabel(member) : userId;
  };

  return (
    <>
      <Toolbar
        left={
          <>
            <FilterSelect
              label="时间段"
              width={140}
              options={RANGES}
              value={days}
              onChange={(next) => setDays(next ?? '30')}
            />
            <FilterSelect
              label="状态"
              clearable
              width={120}
              options={STATUSES}
              value={status}
              onChange={setStatus}
            />
            <FilterSelect
              label="版本"
              clearable
              width={140}
              options={(summary.data ?? []).map((item) => ({ value: item.version, label: item.version }))}
              value={version}
              onChange={setVersion}
            />
          </>
        }
      />
      <section className="kw-detail-section">
        <h3>按版本</h3>
        {summary.isPending ? (
          <Skeleton rows={2} />
        ) : (
          <Table<SkillVersionStats>
            rowKey={(row) => row.version}
            columns={VERSION_COLUMNS}
            rows={summary.data ?? []}
            minWidth={800}
            pageSize={false}
            loading={summary.isFetching}
            empty={
              summary.isError
                ? readableError(summary.error, '运行汇总没有加载出来')
                : '这段时间还没有人用过这个技能'
            }
          />
        )}
      </section>
      <section className="kw-detail-section">
        <h3>最近运行</h3>
        {runs.isPending ? (
          <Skeleton rows={4} />
        ) : (
          <>
            <Table<SkillRunRow>
              rowKey={(row) => row.id}
              columns={runColumns(memberName)}
              rows={rows}
              minWidth={900}
              pageSize={false}
              loading={runs.isFetching && !runs.isFetchingNextPage}
              // 只有对话里的运行能打开那一轮的运行详情
              onRowClick={(row) => (row.conversationId && row.turn !== null ? setOpened(row) : undefined)}
              empty={
                runs.isError
                  ? readableError(runs.error, '运行记录没有加载出来')
                  : status || version
                    ? '没有符合条件的运行'
                    : '这段时间还没有人用过这个技能'
              }
            />
            {rows.length > 0 ? (
              <div className="kw-admin-more">
                <span className="kw-secondary">已加载 {rows.length} 次</span>
                {runs.hasNextPage ? (
                  <Button
                    color="secondary"
                    variant="outline"
                    loading={runs.isFetchingNextPage}
                    onClick={() => void runs.fetchNextPage()}
                  >
                    加载更多
                  </Button>
                ) : null}
              </div>
            ) : null}
          </>
        )}
      </section>
      {opened?.conversationId && opened.turn !== null ? (
        <Suspense fallback={null}>
          <RunInspector
            source={workspaceRun(opened.conversationId)}
            turn={opened.turn}
            title={`${memberName(opened.userId)} · ${opened.title || '未命名会话'} · 第 ${opened.turn} 轮`}
            onClose={() => setOpened(undefined)}
          />
        </Suspense>
      ) : null}
    </>
  );
}
