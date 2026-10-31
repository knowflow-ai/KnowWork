/** 「待审核」：成员提交上来的个人技能，先提交的在前。点「审核」打开抽屉看正文与改动，再通过或退回。 */
import { useMemo, useState } from 'react';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import type { SkillSubmission } from '../../../api/skills';
import { readableError } from '../../../api/http';
import { formatDateTime } from '../../../utils/format';
import { Skeleton } from '../../../ui/Skeleton';
import { Table, type Column } from '../../../ui/Table';
import { useMembers } from '../queries';
import { usePendingSubmissions } from './queries';
import { ReviewSheet } from './ReviewSheet';

export function PendingSubmissions() {
  const [reviewing, setReviewing] = useState<string>();
  const pending = usePendingSubmissions();
  const members = useMembers();
  const columns = useMemo((): readonly Column<SkillSubmission>[] => {
    const nameOf = (userId: string) => {
      const member = members.data?.find((m) => m.userId === userId);
      return member?.name || member?.email || userId;
    };
    return [
      { key: 'name', title: '名称', width: 180, ellipsis: true, render: (row) => row.name },
      {
        key: 'description',
        title: '说明',
        ellipsis: true,
        render: (row) => row.manifest?.description ?? '',
      },
      {
        key: 'version',
        title: '版本',
        width: 110,
        ellipsis: true,
        render: (row) => <span className="kw-num">{row.version}</span>,
      },
      { key: 'by', title: '提交人', width: 120, ellipsis: true, render: (row) => nameOf(row.submittedBy) },
      { key: 'note', title: '附言', width: 200, ellipsis: true, render: (row) => row.note || '-' },
      {
        key: 'at',
        title: '提交时间',
        width: 170,
        render: (row) => <span className="kw-num">{formatDateTime(row.createdAt)}</span>,
      },
      {
        key: 'actions',
        title: '操作',
        width: 90,
        render: (row) => (
          <span className="kw-row-actions">
            <Button color="secondary" variant="ghost" size="sm" onClick={() => setReviewing(row.id)}>
              审核
            </Button>
          </span>
        ),
      },
    ];
  }, [members.data]);
  return (
    <>
      {pending.isPending ? (
        <Skeleton rows={4} />
      ) : (
        <Table<SkillSubmission>
          rowKey={(row) => row.id}
          columns={columns}
          rows={pending.data ?? []}
          minWidth={1000}
          loading={pending.isFetching}
          empty={
            pending.isError
              ? readableError(pending.error, '待审核的技能没有加载出来')
              : '没有待审核的技能。成员在「技能 → 我的技能」里把自己的技能提交到空间后，会出现在这里'
          }
        />
      )}
      <ReviewSheet submissionId={reviewing} onClose={() => setReviewing(undefined)} />
    </>
  );
}
