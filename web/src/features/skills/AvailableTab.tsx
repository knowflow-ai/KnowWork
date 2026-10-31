/** 「可以用的」：我在对话里能用的全部技能，只读。来源：空间（授权给我的）、自带、我的。 */
import { useMemo, useState } from 'react';
import type { AvailableSkill } from '../../api/skills';
import { readableError } from '../../api/http';
import { SearchInput } from '../../ui/SearchInput';
import { Skeleton } from '../../ui/Skeleton';
import { Table, type Column } from '../../ui/Table';
import { Toolbar } from '../admin/AdminPage';
import { filterByKeyword, SOURCE_TEXT } from './model';
import { useAvailableSkills } from './queries';

const COLUMNS: readonly Column<AvailableSkill>[] = [
  { key: 'name', title: '名称', width: 200, ellipsis: true, render: (skill) => skill.name },
  { key: 'description', title: '说明', ellipsis: true, render: (skill) => skill.description },
  { key: 'source', title: '来源', width: 90, render: (skill) => SOURCE_TEXT[skill.source] },
  {
    key: 'version',
    title: '版本',
    width: 130,
    ellipsis: true,
    render: (skill) => <span className="kw-num">{skill.version || '-'}</span>,
  },
];

export function AvailableTab() {
  const [keyword, setKeyword] = useState('');
  const skills = useAvailableSkills();
  const rows = useMemo(() => filterByKeyword(skills.data ?? [], keyword), [skills.data, keyword]);
  return (
    <>
      <Toolbar
        left={<SearchInput placeholder="搜索技能名称或说明" value={keyword} onChange={setKeyword} />}
      />
      {skills.isPending ? (
        <Skeleton rows={6} />
      ) : (
        <Table<AvailableSkill>
          rowKey={(skill) => `${skill.source}:${skill.name}`}
          columns={COLUMNS}
          rows={rows}
          minWidth={720}
          loading={skills.isFetching}
          empty={
            skills.isError
              ? readableError(skills.error, '技能列表没有加载出来')
              : keyword
                ? '没有找到匹配的技能'
                : '还没有能用的技能'
          }
        />
      )}
    </>
  );
}
