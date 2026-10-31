/**
 * 技能页的「企业」页签：到达本空间的企业技能（系统管理员发来的）与内置技能（Word、Excel、PPT）。
 * 默认本空间全体成员都能用；空间管理员可以在本空间停用、改给谁用，不能改内容。停用、收窄只影响本空间。
 * 系统管理员在这里还要管企业技能本身（上传、发布、发给哪些空间……）：扩展经插槽 admin.skills.enterprise 给出
 * 系统管理员看到的页签内容，「本空间」的说法与操作在 here.tsx 里给它复用；没注册时系统管理员也只看到本空间的设置。
 */
import type { UpperSkill } from '../../../api/skills';
import { readableError } from '../../../api/http';
import { slotItems } from '../../../slots/registry';
import { Skeleton } from '../../../ui/Skeleton';
import { Table, type Column } from '../../../ui/Table';
import { useIsSystemAdmin } from '../../brand/queries';
import { RowActions } from '../common';
import { useGrantsList } from '../grants/users';
import { HERE_NOTE, hereText, SOURCE_TEXT, useHereActions } from './here';
import { useUpperSkills } from './queries';

export function UpperSkills() {
  const skills = useUpperSkills();
  const grants = useGrantsList();
  const { actionsOf, dialog } = useHereActions();
  // 列里用到的操作每次渲染都是新的，不做 useMemo
  const columns: readonly Column<UpperSkill>[] = [
    { key: 'name', title: '名称', width: 180, ellipsis: true, render: (skill) => skill.name },
    { key: 'description', title: '说明', ellipsis: true, render: (skill) => skill.description },
    { key: 'source', title: '来源', width: 80, render: (skill) => SOURCE_TEXT[skill.source] },
    {
      key: 'version',
      title: '版本',
      width: 110,
      ellipsis: true,
      render: (skill) => <span className="kw-num">{skill.version || '-'}</span>,
    },
    { key: 'users', title: '给谁用', width: 130, render: (skill) => hereText(skill, grants.data) },
    {
      key: 'actions',
      title: '操作',
      width: 200,
      render: (skill) => <RowActions actions={actionsOf(skill)} />,
    },
  ];
  if (skills.isPending) return <Skeleton rows={4} />;
  return (
    <>
      <p className="kw-detail-note">{HERE_NOTE}</p>
      <Table<UpperSkill>
        rowKey={(skill) => skill.key}
        columns={columns}
        rows={skills.data ?? []}
        minWidth={880}
        loading={skills.isFetching}
        empty={
          skills.isError ? readableError(skills.error, '企业技能没有加载出来') : '没有企业技能或内置技能'
        }
      />
      {dialog}
    </>
  );
}

/** 「企业」页签：系统管理员看扩展给的管理视图（含本空间的设置），其他管理员看本空间的设置。 */
export function EnterpriseSkills() {
  const isSystemAdmin = useIsSystemAdmin().data === true;
  const [panel] = slotItems('admin.skills.enterprise');
  return isSystemAdmin && panel ? <>{panel.children}</> : <UpperSkills />;
}
