/** 技能管理（空间技能）：上传 ZIP 技能包成为草稿，发布后成员在对话里就能用；可停用、上传新版本、删除。 */
import { useMemo, useRef, useState } from 'react';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { FileUpload } from '@openai/apps-sdk-ui/components/Icon';
import type { Skill } from '../../../api/skills';
import { readableError } from '../../../api/http';
import { formatDateTime } from '../../../utils/format';
import { SearchInput } from '../../../ui/SearchInput';
import { Skeleton } from '../../../ui/Skeleton';
import { StatusDot } from '../../../ui/StatusDot';
import { Table, type Column } from '../../../ui/Table';
import { AdminPage, Toolbar } from '../AdminPage';
import { useSkillActions, useSkills } from './queries';
import { SkillActions } from './SkillActions';
import { SkillDetail } from './SkillDetail';
import { filterSkills, SKILL_STATE, skillState } from './skillsModel';
import { ZipPicker, type ZipPickerHandle } from './ZipPicker';

function columns(onDetail: (id: string) => void): readonly Column<Skill>[] {
  return [
    { key: 'name', title: '名称', width: 180, ellipsis: true, render: (skill) => skill.name },
    { key: 'description', title: '说明', ellipsis: true, render: (skill) => skill.description },
    {
      key: 'state',
      title: '状态',
      width: 160,
      render: (skill) => {
        const state = SKILL_STATE[skillState(skill)];
        return (
          <span className="kw-labeled">
            <StatusDot tone={state.status}>{state.text}</StatusDot>
            {skill.status === 'published' && skill.hasUnpublishedChanges ? (
              <span className="kw-secondary-text">有新草稿</span>
            ) : null}
          </span>
        );
      },
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

export default function SkillsPage() {
  const [keyword, setKeyword] = useState('');
  const [detailId, setDetailId] = useState<string | undefined>();
  const skills = useSkills();
  const { install } = useSkillActions();
  const picker = useRef<ZipPickerHandle>(null);
  const rows = useMemo(() => filterSkills(skills.data ?? [], keyword), [skills.data, keyword]);
  const tableColumns = useMemo(() => columns(setDetailId), []);

  return (
    <AdminPage title="技能" description="把常用的工作方法沉淀成技能，发布后成员在对话里就能直接使用。">
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
          minWidth={960}
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
    </AdminPage>
  );
}
