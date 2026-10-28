/**
 * 问答大盘（管理后台 → 运营）：智能体跑得怎么样、哪里出问题。只给空间所有者和管理员，服务端判定。
 * 上面四个数各和上一段同样长的时间比，两条趋势；下面是需要关注的（失败、取消、被点踩、检索了没引用），
 * 以及工具、知识库两个维度。数字都能点到运行记录里具体是哪几轮（已按条件筛好）。
 */
import { Warning } from '@openai/apps-sdk-ui/components/Icon';
import { useState } from 'react';
import { SegmentedControl } from '@openai/apps-sdk-ui/components/SegmentedControl';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { getInsights, INSIGHT_RANGES, type InsightRange, type Insights } from '../../../api/insights';
import { Empty } from '../../../ui/Empty';
import { Metric, MetricGrid } from '../../../ui/Metric';
import { Skeleton } from '../../../ui/Skeleton';
import { TrendChart } from '../../../ui/TrendChart';
import { cx } from '../../../ui/cx';
import { AdminPage, Toolbar } from '../AdminPage';
import { changeOf, completionRate, endedOf, number, percent, rateChangeOf } from './measures';
import { BaseTable, Concerns, ToolTable } from './sections';

function Overview({ data }: { readonly data: Insights }) {
  const { totals, previous } = data;
  const rate = completionRate(totals.completed, endedOf(totals));
  return (
    <MetricGrid>
      <Metric
        label="提问"
        value={`${number(totals.turns)} 轮`}
        hint={changeOf(totals.turns, previous.turns, ' 轮')}
      />
      <Metric
        label="提问人数"
        value={number(totals.askers)}
        hint={changeOf(totals.askers, previous.askers, ' 人')}
      />
      <Metric
        label="完成率"
        value={percent(rate)}
        // 口径写出来：正常答完占已结束（答完 + 失败 + 取消）的比例，只说明跑没跑完，不代表答得对不对
        hint={`答完 ${number(totals.completed)} · 失败 ${number(totals.failed)} · 取消 ${number(totals.cancelled)}，${rateChangeOf(rate, completionRate(previous.completed, endedOf(previous)))}`}
      />
      <Metric
        label="估算费用"
        value={totals.cost}
        hint={`输入 ${number(totals.inputTokens)} · 输出 ${number(totals.outputTokens)} token`}
      />
    </MetricGrid>
  );
}

function Trends({ data }: { readonly data: Insights }) {
  const label = (date: string) => date.slice(5); // 只写月-日
  return (
    <div className="kw-insights-pair">
      <TrendChart
        title="每天的提问"
        points={data.days.map((d) => ({ label: label(d.date), value: d.turns }))}
      />
      <TrendChart
        title="每天的完成率"
        max={1}
        format={(v) => percent(v)}
        points={data.days.map((d) => ({ label: label(d.date), value: completionRate(d.completed, d.ended) }))}
      />
    </div>
  );
}

function Body({ data, refreshing }: { readonly data: Insights; readonly refreshing: boolean }) {
  return (
    <div className={cx('kw-insights', refreshing ? 'is-refreshing' : undefined)}>
      <Overview data={data} />
      <Trends data={data} />
      <section aria-label="需要关注">
        <h2>需要关注</h2>
        <Concerns data={data} />
      </section>
      <div className="kw-insights-pair">
        <section aria-label="工具">
          <h2>工具</h2>
          <ToolTable tools={data.tools} window={data} />
        </section>
        <section aria-label="知识库">
          <h2>知识库</h2>
          <BaseTable bases={data.bases} window={data} />
        </section>
      </div>
    </div>
  );
}

export default function InsightsPage() {
  const [days, setDays] = useState<InsightRange>(7);
  const insights = useQuery({
    queryKey: ['admin', 'insights', days],
    queryFn: () => getInsights(days),
    placeholderData: keepPreviousData,
  });
  const data = insights.data;
  return (
    <AdminPage
      title="问答大盘"
      description="智能体跑得怎么样、哪里出问题。数字都能点到运行记录里具体是哪几轮。"
    >
      <Toolbar
        left={
          <>
            <SegmentedControl<string>
              aria-label="时间范围"
              value={String(days)}
              onChange={(value) => setDays(Number(value) as InsightRange)}
            >
              {INSIGHT_RANGES.map((n) => (
                <SegmentedControl.Option key={n} value={String(n)}>
                  最近 {n} 天
                </SegmentedControl.Option>
              ))}
            </SegmentedControl>
            {data ? (
              <span className="kw-secondary">
                {data.start} 至 {data.end}
              </span>
            ) : null}
          </>
        }
      />
      {data ? (
        <Body data={data} refreshing={insights.isPlaceholderData} />
      ) : insights.isError ? (
        <Empty area icon={<Warning />} title="问答大盘加载失败" description="请稍后刷新重试" />
      ) : (
        <Skeleton rows={6} />
      )}
    </AdminPage>
  );
}
