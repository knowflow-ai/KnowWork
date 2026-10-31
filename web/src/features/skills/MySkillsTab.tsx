/**
 * 「我的技能」：自己上传的个人技能，只有自己能用，上传即生效。状态写明对话里有没有生效、没生效为什么；
 * 好用的可以提交到空间，空间管理员审核通过后复制成空间技能。
 */
import { useMemo, useRef, useState } from 'react';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { FileUpload } from '@openai/apps-sdk-ui/components/Icon';
import type { Skill } from '../../api/skills';
import { readableError } from '../../api/http';
import { formatDateTime } from '../../utils/format';
import { SearchInput } from '../../ui/SearchInput';
import { Skeleton } from '../../ui/Skeleton';
import { Table, type Column } from '../../ui/Table';
import { Toolbar } from '../admin/AdminPage';
import { ZipPicker, type ZipPickerHandle } from '../admin/skills/ZipPicker';
import { filterByKeyword, mySkillState, submissionState, type StateText } from './model';
import { MySkillActions } from './MySkillActions';
import { MySkillDetail } from './MySkillDetail';
import { useMySkillActions, useMySkills } from './queries';

function State({ state }: { readonly state: StateText | undefined }) {
  if (!state) return <span className="kw-secondary-text">-</span>;
  return (
    <span className="kw-labeled" title={state.note}>
      <span>{state.text}</span>
      {state.note ? <span className="kw-secondary-text">{state.note}</span> : null}
    </span>
  );
}

function columns(onDetail: (id: string) => void): readonly Column<Skill>[] {
  return [
    { key: 'name', title: '名称', width: 180, ellipsis: true, render: (skill) => skill.name },
    { key: 'description', title: '说明', ellipsis: true, render: (skill) => skill.description },
    {
      key: 'state',
      title: '状态',
      width: 200,
      ellipsis: true,
      render: (skill) => <State state={mySkillState(skill)} />,
    },
    {
      key: 'version',
      title: '版本',
      width: 120,
      ellipsis: true,
      render: (skill) => <span className="kw-num">{skill.publishedVersion ?? '-'}</span>,
    },
    {
      key: 'submission',
      title: '提交到空间',
      width: 200,
      ellipsis: true,
      render: (skill) => <State state={submissionState(skill)} />,
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
      width: 180,
      render: (skill) => <MySkillActions skill={skill} onDetail={() => onDetail(skill.id)} />,
    },
  ];
}

export function MySkillsTab() {
  const [keyword, setKeyword] = useState('');
  const [detailId, setDetailId] = useState<string | undefined>();
  const skills = useMySkills();
  const { install } = useMySkillActions();
  const picker = useRef<ZipPickerHandle>(null);
  const rows = useMemo(() => filterByKeyword(skills.data ?? [], keyword), [skills.data, keyword]);
  const tableColumns = useMemo(() => columns(setDetailId), []);
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
          minWidth={1100}
          loading={skills.isFetching}
          empty={
            skills.isError
              ? readableError(skills.error, '技能列表没有加载出来')
              : keyword
                ? '没有找到匹配的技能'
                : '还没有自己的技能。上传一个包含 SKILL.md 的 ZIP 技能包，只有你自己能用；好用的话可以提交到空间'
          }
        />
      )}
      <MySkillDetail
        listed={skills.data?.find((skill) => skill.id === detailId)}
        onClose={() => setDetailId(undefined)}
      />
    </>
  );
}
