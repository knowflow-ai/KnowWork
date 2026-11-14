/**
 * 全空间用量：按成员、模型或天汇总。扩展可以经插槽 admin.usage.tabs 在后面加标签页（例如额度：看了谁用得多，就地给他定额度、
 * 加额，不用换菜单）；没有扩展的标签页时就是这一张统计，不出标签栏。网址里的 tab 指定打开哪一页，不认识的打开「统计」。
 */
import { useSearchParams } from 'react-router';
import { fetchUsage } from '../../api/models';
import { memberLabel } from '../../api/workspace';
import { slotItems } from '../../slots/registry';
import { Tabs } from '../../ui/Tabs';
import { UsageView } from '../usage/UsageView';
import { AdminPage } from './AdminPage';
import { KEYS, useMembers } from './queries';

const GROUPINGS = [
  { value: 'user', label: '按成员' },
  { value: 'model', label: '按模型' },
  { value: 'day', label: '按天' },
] as const;

const STATS = 'stats';

function UsageStats() {
  const members = useMembers();
  const labelOf = (groupBy: string, key: string) => {
    if (groupBy !== 'user') return key;
    const member = members.data?.find((m) => m.userId === key);
    return member ? memberLabel(member) : key;
  };
  return <UsageView queryKey={KEYS.usage} fetcher={fetchUsage} groupings={GROUPINGS} labelOf={labelOf} />;
}

export default function UsagePage() {
  const [params, setParams] = useSearchParams();
  const extra = slotItems('admin.usage.tabs');
  const items = [{ key: STATS, label: '统计', children: <UsageStats /> }, ...extra];
  const value = items.find((item) => item.key === params.get('tab'))?.key ?? STATS;
  return (
    <AdminPage title="用量" description="模型调用次数、token 与按单价估算的费用。没填单价的模型费用记为 0。">
      {extra.length === 0 ? (
        <UsageStats />
      ) : (
        <Tabs items={items} value={value} onChange={(tab) => setParams({ tab }, { replace: true })} />
      )}
    </AdminPage>
  );
}
