/**
 * 技能详情抽屉。「概览」：说明、版本、可调用的工具、需要的环境变量、发布记录；
 * 「最近运行」：按版本汇总与每一次运行，只给空间管理员看（与运行记录同一口径）。
 */
import { Badge } from '@openai/apps-sdk-ui/components/Badge';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { useState } from 'react';
import type { Skill, SkillEnv, SkillManifest, SkillRelease } from '../../../api/skills';
import { readableError } from '../../../api/http';
import { formatDateTime } from '../../../utils/format';
import { DescriptionList } from '../../../ui/DescriptionList';
import { Sheet } from '../../../ui/Sheet';
import { Skeleton } from '../../../ui/Skeleton';
import { Table } from '../../../ui/Table';
import { Tabs } from '../../../ui/Tabs';
import { useIsWorkspaceAdmin } from '../../auth/queries';
import { useMembers } from '../queries';
import { EnvDialog } from './EnvDialog';
import { UsersDialog } from '../grants/UsersSelect';
import { useGrantsList, usersOf, usersText, useUsersAction } from '../grants/users';
import { useSkillActions, useSkillDetail } from './queries';
import { SkillRuns } from './SkillRuns';
import { SKILL_STATE, skillState } from './skillsModel';

function Tools({ names, none }: { readonly names: readonly string[]; readonly none: string }) {
  if (names.length === 0) return <span className="kw-secondary-text">{none}</span>;
  return (
    <span className="kw-tags">
      {names.map((name) => (
        <Badge key={name} color="secondary" variant="soft" size="sm">
          {name}
        </Badge>
      ))}
    </span>
  );
}

interface EnvEditing {
  /** 已经填了值的名字 */
  readonly filled: readonly string[];
  /** 有权限填时给：打开填写对话框 */
  readonly onEdit?: () => void;
}

export function Manifest({ manifest, env }: { readonly manifest: SkillManifest; readonly env?: EnvEditing }) {
  return (
    <>
      <DescriptionList
        items={[
          { label: '说明', value: manifest.description },
          { label: '可调用的工具', value: <Tools names={manifest.allowedTools} none="不限" /> },
          { label: '完成判据', value: <Tools names={manifest.completionTools} none="没有声明" /> },
        ]}
      />
      {manifest.env.length > 0 ? (
        <section className="kw-detail-section">
          <div className="kw-detail-section-head">
            <h3>需要的环境变量</h3>
            {env?.onEdit ? (
              <Button color="secondary" variant="outline" size="sm" onClick={env.onEdit}>
                填写环境变量
              </Button>
            ) : null}
          </div>
          <Table
            rowKey={(row) => row.name}
            pageSize={false}
            rows={manifest.env}
            columns={[
              { key: 'name', title: '名称', width: 200, render: (row) => row.name },
              { key: 'required', title: '必填', width: 70, render: (row) => (row.required ? '是' : '否') },
              ...(env
                ? [
                    {
                      key: 'filled',
                      title: '值',
                      width: 90,
                      render: (row: SkillEnv) =>
                        env.filled.includes(row.name) ? (
                          '已填写'
                        ) : (
                          <span className="kw-secondary-text">未填</span>
                        ),
                    },
                  ]
                : []),
              { key: 'description', title: '说明', ellipsis: true, render: (row) => row.description },
            ]}
          />
        </section>
      ) : null}
    </>
  );
}

function Overview({
  skill,
  releases,
}: {
  readonly skill: Skill;
  readonly releases: readonly SkillRelease[];
}) {
  const members = useMembers();
  const nameOf = (userId: string) => {
    const member = members.data?.find((m) => m.userId === userId);
    return member?.name || member?.email || userId;
  };
  const manifest = skill.draft ?? skill.published;
  const { env } = useSkillActions();
  const [editingEnv, setEditingEnv] = useState(false);
  const grants = useGrantsList();
  const saveUsers = useUsersAction();
  const [editingUsers, setEditingUsers] = useState(false);
  const users = usersOf(grants.data, `skill:${skill.id}`);
  return (
    <>
      <DescriptionList
        className="kw-detail-summary"
        items={[
          { label: '状态', value: SKILL_STATE[skillState(skill)].text },
          { label: '当前发布版本', value: skill.publishedVersion ?? '还没发布' },
          { label: '草稿版本', value: skill.draft?.version || '未写版本号' },
          { label: '有未发布的修改', value: skill.hasUnpublishedChanges ? '是' : '否' },
          {
            label: '给谁用',
            value: (
              <span className="kw-labeled">
                <span>{usersText(users)}</span>
                <Button color="secondary" variant="ghost" size="sm" onClick={() => setEditingUsers(true)}>
                  修改
                </Button>
              </span>
            ),
          },
        ]}
      />
      {editingUsers ? (
        <UsersDialog
          title={`「${skill.name}」给谁用`}
          initial={users}
          pending={saveUsers.pending}
          onSave={(subjects) => saveUsers.run({ resource: `skill:${skill.id}`, subjects })}
          onClose={() => setEditingUsers(false)}
        />
      ) : null}
      {manifest ? (
        <Manifest manifest={manifest} env={{ filled: skill.envFilled, onEdit: () => setEditingEnv(true) }} />
      ) : null}
      {editingEnv && manifest ? (
        <EnvDialog
          name={skill.name}
          env={manifest.env}
          filled={skill.envFilled}
          pending={env.pending}
          onSave={(values) => env.run({ skill, values })}
          onClose={() => setEditingEnv(false)}
        />
      ) : null}
      <section className="kw-detail-section">
        <h3>发布记录</h3>
        <Table
          rowKey={(row) => row.id}
          pageSize={false}
          empty="还没有发布过"
          rows={releases}
          columns={[
            {
              key: 'version',
              title: '版本',
              width: 140,
              render: (row) => <span className="kw-num">{row.version}</span>,
            },
            { key: 'by', title: '发布人', ellipsis: true, render: (row) => nameOf(row.releasedBy) },
            {
              key: 'at',
              title: '发布时间',
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

export function SkillDetail({
  skillId,
  onClose,
}: {
  readonly skillId?: string;
  readonly onClose: () => void;
}) {
  const detail = useSkillDetail(skillId);
  const admin = useIsWorkspaceAdmin(skillId !== undefined);
  if (skillId === undefined) return null;
  const data = detail.data;
  const overview = data ? <Overview skill={data.skill} releases={data.releases} /> : null;
  return (
    <Sheet title={data?.skill.name ?? '技能详情'} width={960} onClose={onClose}>
      {detail.isPending ? <Skeleton rows={6} /> : null}
      {detail.isError ? (
        <p className="kw-secondary-text" role="alert">
          {readableError(detail.error, '技能详情没有加载出来')}
        </p>
      ) : null}
      {overview && admin.data === true ? (
        <Tabs
          items={[
            { key: 'overview', label: '概览', children: overview },
            { key: 'runs', label: '最近运行', children: <SkillRuns skillId={skillId} /> },
          ]}
        />
      ) : (
        overview
      )}
    </Sheet>
  );
}
