/**
 * 技能管理（空间技能）：上传 ZIP 技能包成为草稿，发布后成员在对话里就能用；可停用、上传新版本、删除。
 * 「待审核」是成员从「我的技能」提交上来的，通过后复制成空间技能。网址里的 tab 指定打开哪一页。
 */
import { useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { FileUpload } from '@openai/apps-sdk-ui/components/Icon';
import type { Skill } from '../../../api/skills';
import { readableError } from '../../../api/http';
import { formatDateTime } from '../../../utils/format';
import { SearchInput } from '../../../ui/SearchInput';
import { Skeleton } from '../../../ui/Skeleton';
import { StatusDot } from '../../../ui/StatusDot';
import { Table, type Column } from '../../../ui/Table';
import { Tabs } from '../../../ui/Tabs';
import { AdminPage, Toolbar } from '../AdminPage';
import { PendingSubmissions } from './PendingSubmissions';
import { EnterpriseSkills } from './UpperSkills';
import type { Grant } from '../../../api/workspace';
import { useGrantsList, usersOf, usersText } from '../grants/users';
import { usePendingSubmissions, useSkillActions, useSkills } from './queries';
import { SkillActions } from './SkillActions';
import { SkillDetail } from './SkillDetail';
import { envNote, filterSkills, shadowNote, SKILL_STATE, skillState } from './skillsModel';
import { ZipPicker, type ZipPickerHandle } from './ZipPicker';

function columns(
  onDetail: (id: string) => void,
  grants: readonly Grant[] | undefined,
): readonly Column<Skill>[] {
  return [
    { key: 'name', title: '名称', width: 180, ellipsis: true, render: (skill) => skill.name },
    { key: 'description', title: '说明', ellipsis: true, render: (skill) => skill.description },
    {
      key: 'state',
      title: '状态',
      width: 200,
      ellipsis: true,
      render: (skill) => {
        const state = SKILL_STATE[skillState(skill)];
        const note =
          shadowNote(skill) ??
          envNote(skill) ??
          (skill.status === 'published' && skill.hasUnpublishedChanges ? '有新草稿' : undefined);
        return (
          <span className="kw-labeled" title={note}>
            <StatusDot tone={state.status}>{state.text}</StatusDot>
            {note ? <span className="kw-secondary-text">{note}</span> : null}
          </span>
        );
      },
    },
    {
      key: 'users',
      title: '给谁用',
      width: 110,
      ellipsis: true,
      render: (skill) => usersText(usersOf(grants, `skill:${skill.id}`)),
    },
    {
      key: 'version',
      title: '发布版本',
      width: 110,
      render: (skill) => <span className="kw-num">{skill.publishedVersion ?? '-'}</span>,
    },
    {
      key: 'updated',
      title: '更新时间',
      width: 170,
      render: (skill) => (
        <span className="kw-num">{skill.updatedAt ? formatDateTime(skill.updatedAt) : '-'}</span>
      ),
    },
    {
      key: 'actions',
      title: '操作',
      width: 200,
      render: (skill) => <SkillActions skill={skill} onDetail={() => onDetail(skill.id)} />,
    },
  ];
}

function WorkspaceSkills() {
  const [keyword, setKeyword] = useState('');
  const [detailId, setDetailId] = useState<string | undefined>();
  const skills = useSkills();
  const { install } = useSkillActions();
  const picker = useRef<ZipPickerHandle>(null);
  const rows = useMemo(() => filterSkills(skills.data ?? [], keyword), [skills.data, keyword]);
  const grants = useGrantsList();
  const tableColumns = useMemo(() => columns(setDetailId, grants.data), [grants.data]);

  return (
    <>
      <Toolbar
        left={<SearchInput placeholder="搜索技能名称或说明" value={keyword} onChange={setKeyword} />}
        right={
          <>
            <Button color="primary" loading={install.pending} onClick={() => picker.current?.open()}>
              <FileUpload />
              上传技能包
            </Button>
            <ZipPicker ref={picker} onPick={(file) => void install.run(file)} />
          </>
        }
      />
      {skills.isPending ? (
        <Skeleton rows={6} />
      ) : (
        <Table<Skill>
          rowKey={(skill) => skill.id}
          columns={tableColumns}
          rows={rows}
          minWidth={1060}
          loading={skills.isFetching}
          empty={
            skills.isError
              ? readableError(skills.error, '技能列表没有加载出来')
              : keyword
                ? '没有找到匹配的技能'
                : '还没有技能。上传一个包含 SKILL.md 的 ZIP 技能包开始'
          }
        />
      )}
      <SkillDetail skillId={detailId} onClose={() => setDetailId(undefined)} />
    </>
  );
}

export default function SkillsPage() {
  const [params, setParams] = useSearchParams();
  const pending = usePendingSubmissions();
  const count = pending.data?.length ?? 0;
  const items = [
    { key: 'all', label: '空间技能', children: <WorkspaceSkills /> },
    { key: 'upper', label: '企业', children: <EnterpriseSkills /> },
    { key: 'pending', label: count > 0 ? `待审核 ${count}` : '待审核', children: <PendingSubmissions /> },
  ];
  const value = items.find((item) => item.key === params.get('tab'))?.key ?? 'all';
  return (
    <AdminPage title="技能" description="把常用的工作方法沉淀成技能，发布后成员在对话里就能直接使用。">
      <Tabs items={items} value={value} onChange={(tab) => setParams({ tab }, { replace: true })} />
    </AdminPage>
  );
}
