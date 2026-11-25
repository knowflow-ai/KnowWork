/** 一条审计的详情（右侧抽屉）：时间、操作者、动作、对象、结果，加上这条记下的详情（敏感词拦截的方向、命中词等）。 */
import type { AuditEntry } from '../../../api/audit';
import { formatDateTime } from '../../../utils/format';
import { DescriptionList } from '../../../ui/DescriptionList';
import { Sheet } from '../../../ui/Sheet';
import { actionLabel, actorTypeLabel, detailLines, RESULT_BADGE } from './labels';

export function AuditDetail({
  entry,
  actorName,
  onClose,
}: {
  readonly entry: AuditEntry;
  readonly actorName: string;
  readonly onClose: () => void;
}) {
  const result = RESULT_BADGE[entry.result]?.text ?? entry.result;
  const resource = entry.resourceType
    ? `${entry.resourceType}${entry.resourceId ? `：${entry.resourceId}` : ''}`
    : '—';
  return (
    <Sheet title={actionLabel(entry.action)} width={520} onClose={onClose}>
      <DescriptionList
        items={[
          { label: '时间', value: formatDateTime(entry.occurredAt) },
          { label: '操作者', value: `${actorName}（${actorTypeLabel(entry.actorType)}）` },
          { label: '动作', value: entry.action },
          { label: '对象', value: resource },
          { label: '结果', value: result },
          ...detailLines(entry.action, entry.detail).map((line) => ({
            ...line,
            key: `detail-${line.label}`,
          })),
        ]}
      />
    </Sheet>
  );
}
