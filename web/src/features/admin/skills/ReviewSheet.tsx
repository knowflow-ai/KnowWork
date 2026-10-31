/**
 * 审核一份技能提交的抽屉：谁提交的、附言、进空间是新建还是更新；SKILL.md 正文（空间里已有那份时逐行对比）、
 * 解锁的工具、技能包里文件的增删改，可以下载整个包看脚本。标题右侧是下载、不通过、通过。
 */
import { useState } from 'react';
import { Button, ButtonLink } from '@openai/apps-sdk-ui/components/Button';
import { Download } from '@openai/apps-sdk-ui/components/Icon';
import { submissionArchiveUrl, type SkillFile, type SubmissionDetail } from '../../../api/skills';
import { readableError } from '../../../api/http';
import { formatBytes, formatDateTime } from '../../../utils/format';
import { DescriptionList } from '../../../ui/DescriptionList';
import { Sheet } from '../../../ui/Sheet';
import { Skeleton } from '../../../ui/Skeleton';
import { Table } from '../../../ui/Table';
import { cx } from '../../../ui/cx';
import { useMembers } from '../queries';
import { fileChanges, lineDiff, type DiffLine, type FileChange } from './diff';
import { useSubmission } from './queries';
import { ApproveDialog, RejectDialog } from './ReviewDialogs';
import { Manifest } from './SkillDetail';

const CHANGE_TEXT: Record<FileChange, string> = {
  added: '新增',
  removed: '删除',
  changed: '改动',
  same: '未变',
};
const MARK = { same: ' ', add: '+', del: '-' } as const;

/** 空间里还没有这个技能（before 为 null）时只列全文，不标加减号。 */
function SkillText({ before, after }: { readonly before: string | null; readonly after: string }) {
  const lines: readonly DiffLine[] =
    before === null ? after.split('\n').map((text) => ({ kind: 'same', text })) : lineDiff(before, after);
  return (
    <pre className="kw-skill-text" aria-label="SKILL.md 正文">
      {lines.map((line, index) => (
        <div key={index} className={cx(line.kind === 'add' && 'is-add', line.kind === 'del' && 'is-del')}>
          {before === null ? '' : `${MARK[line.kind]} `}
          {line.text || ' '}
        </div>
      ))}
    </pre>
  );
}

function Files({
  before,
  after,
}: {
  readonly before: readonly SkillFile[] | null;
  readonly after: readonly SkillFile[];
}) {
  return (
    <Table
      rowKey={(row) => row.path}
      pageSize={false}
      rows={fileChanges(before, after)}
      columns={[
        { key: 'path', title: '文件', ellipsis: true, render: (row) => row.path },
        {
          key: 'size',
          title: '大小',
          width: 100,
          render: (row) => <span className="kw-num">{formatBytes(row.size)}</span>,
        },
        {
          key: 'change',
          title: '变化',
          width: 80,
          render: (row) =>
            row.change === 'same' ? <span className="kw-secondary-text">未变</span> : CHANGE_TEXT[row.change],
        },
      ]}
    />
  );
}

function where(detail: SubmissionDetail): string {
  if (detail.nameTaken) return '空间里已经有同名技能，这份提交通过不了，请退回让作者改名';
  if (!detail.target) return '新技能：通过后在空间里新建';
  const current = detail.target.publishedVersion ? `当前发布 ${detail.target.publishedVersion}` : '还没发布';
  return `已有（${current}）：通过后成为它的新版本`;
}

function Review({ detail }: { readonly detail: SubmissionDetail }) {
  const members = useMembers();
  const who = members.data?.find((m) => m.userId === detail.submission.submittedBy);
  const manifest = detail.submission.manifest;
  const target = detail.target;
  return (
    <>
      <DescriptionList
        className="kw-detail-summary"
        items={[
          { label: '提交人', value: who?.name || who?.email || detail.submission.submittedBy },
          { label: '版本', value: detail.submission.version },
          { label: '提交时间', value: formatDateTime(detail.submission.createdAt) },
          { label: '附言', value: detail.submission.note || '没写' },
          { label: '空间里', value: where(detail) },
        ]}
      />
      {target?.hasUnpublishedChanges ? (
        <p className="kw-detail-note">空间里这份技能有还没发布的修改，通过会覆盖掉。</p>
      ) : null}
      {manifest ? <Manifest manifest={manifest} /> : null}
      <section className="kw-detail-section">
        <h3>{target ? 'SKILL.md 正文（和空间里那份比，+ 是加上的，- 是删掉的）' : 'SKILL.md 正文'}</h3>
        <SkillText before={target ? target.content : null} after={detail.content} />
      </section>
      <section className="kw-detail-section">
        <h3>技能包里的文件</h3>
        <Files before={target?.manifest?.files ?? null} after={manifest?.files ?? []} />
      </section>
    </>
  );
}

export function ReviewSheet({
  submissionId,
  onClose,
}: {
  readonly submissionId?: string;
  readonly onClose: () => void;
}) {
  const detail = useSubmission(submissionId);
  const [deciding, setDeciding] = useState<'approve' | 'reject'>();
  if (submissionId === undefined) return null;
  const data = detail.data;
  const done = (ok: boolean) => {
    setDeciding(undefined);
    if (ok) onClose();
  };
  const actions = data ? (
    <>
      <ButtonLink
        as="a"
        href={submissionArchiveUrl(data.submission.id)}
        download
        color="secondary"
        variant="ghost"
        size="sm"
      >
        <Download />
        下载技能包
      </ButtonLink>
      <Button color="secondary" variant="outline" size="sm" onClick={() => setDeciding('reject')}>
        不通过
      </Button>
      <Button color="primary" size="sm" disabled={data.nameTaken} onClick={() => setDeciding('approve')}>
        通过
      </Button>
    </>
  ) : null;
  return (
    <Sheet
      title={data ? `审核「${data.submission.name}」` : '审核技能'}
      width={960}
      extra={actions}
      onClose={onClose}
    >
      {detail.isPending ? <Skeleton rows={8} /> : null}
      {detail.isError ? (
        <p className="kw-secondary-text" role="alert">
          {readableError(detail.error, '提交详情没有加载出来')}
        </p>
      ) : null}
      {data ? <Review detail={data} /> : null}
      {data && deciding === 'approve' ? <ApproveDialog detail={data} onDone={done} /> : null}
      {data && deciding === 'reject' ? <RejectDialog detail={data} onDone={done} /> : null}
    </Sheet>
  );
}
