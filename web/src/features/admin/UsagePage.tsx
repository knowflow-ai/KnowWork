/** 全空间用量：按成员、模型或天汇总。 */
import { fetchUsage } from '../../api/models';
import { memberLabel } from '../../api/workspace';
import { UsageView } from '../usage/UsageView';
import { AdminPage } from './AdminPage';
import { KEYS, useMembers } from './queries';

const GROUPINGS = [
  { value: 'user', label: '按成员' },
  { value: 'model', label: '按模型' },
  { value: 'day', label: '按天' },
] as const;

export default function UsagePage() {
  const members = useMembers();
  const labelOf = (groupBy: string, key: string) => {
    if (groupBy !== 'user') return key;
    const member = members.data?.find((m) => m.userId === key);
    return member ? memberLabel(member) : key;
  };
  return (
    <AdminPage title="用量" description="模型调用次数、token 与按单价估算的费用。没填单价的模型费用记为 0。">
      <UsageView queryKey={KEYS.usage} fetcher={fetchUsage} groupings={GROUPINGS} labelOf={labelOf} />
    </AdminPage>
  );
}
