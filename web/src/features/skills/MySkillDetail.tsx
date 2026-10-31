/** 我的技能详情抽屉：状态、提交到空间的进度、说明与工具、以前的版本。 */
import type { Skill, SkillRelease } from '../../api/skills';
import { readableError } from '../../api/http';
import { formatDateTime } from '../../utils/format';
import { DescriptionList } from '../../ui/DescriptionList';
import { Sheet } from '../../ui/Sheet';
import { Skeleton } from '../../ui/Skeleton';
import { Table } from '../../ui/Table';
import { useState } from 'react';
import { EnvDialog } from '../admin/skills/EnvDialog';
import { Manifest } from '../admin/skills/SkillDetail';
import { mySkillState, submissionState, type StateText } from './model';
import { useMySkillActions, useMySkillDetail } from './queries';

const line = (state: StateText | undefined) =>
  state ? [state.text, state.note].filter(Boolean).join('：') : '没提交过';

function Overview({
  listed,
  skill,
  releases,
}: {
  readonly listed: Skill;
  readonly skill: Skill;
  readonly releases: readonly SkillRelease[];
}) {
  const manifest = skill.published ?? skill.draft;
  const { env } = useMySkillActions();
  const [editingEnv, setEditingEnv] = useState(false);
  return (
    <>
      <DescriptionList
        className="kw-detail-summary"
        items={[
          { label: '状态', value: line(mySkillState(listed)) },
          { label: '当前版本', value: skill.publishedVersion ?? '-' },
          { label: '提交到空间', value: line(submissionState(listed)) },
        ]}
      />
      {manifest ? (
        <Manifest manifest={manifest} env={{ filled: listed.envFilled, onEdit: () => setEditingEnv(true) }} />
      ) : null}
      {editingEnv && manifest ? (
        <EnvDialog
          name={skill.name}
          env={manifest.env}
          filled={listed.envFilled}
          pending={env.pending}
          onSave={(values) => env.run({ skill, values })}
          onClose={() => setEditingEnv(false)}
        />
      ) : null}
      <section className="kw-detail-section">
        <h3>以前的版本</h3>
        <Table
          rowKey={(row) => row.id}
          pageSize={false}
          empty="还没有版本"
          rows={releases}
          columns={[
            { key: 'version', title: '版本', render: (row) => <span className="kw-num">{row.version}</span> },
            {
              key: 'at',
              title: '上传时间',
              width: 180,
              render: (row) => (
                <span className="kw-num">{row.releasedAt ? formatDateTime(row.releasedAt) : '-'}</span>
              ),
            },
          ]}
        />
      </section>
    </>
  );
}

/** listed：列表里的那一行。生效状态与提交进度只在列表接口里有，详情接口给清单与以前的版本。 */
export function MySkillDetail({
  listed,
  onClose,
}: {
  readonly listed?: Skill;
  readonly onClose: () => void;
}) {
  const detail = useMySkillDetail(listed?.id);
  if (listed === undefined) return null;
  const data = detail.data;
  return (
    <Sheet title={listed.name} width={720} onClose={onClose}>
      {detail.isPending ? <Skeleton rows={6} /> : null}
      {detail.isError ? (
        <p className="kw-secondary-text" role="alert">
          {readableError(detail.error, '技能详情没有加载出来')}
        </p>
      ) : null}
      {data ? <Overview listed={listed} skill={data.skill} releases={data.releases} /> : null}
    </Sheet>
  );
}
