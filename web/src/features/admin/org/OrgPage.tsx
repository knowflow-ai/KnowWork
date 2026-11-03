/**
 * 部门与用户组。部门、用户组是本空间自己建的分组；接了企业目录时最后多一个只读的「组织架构」（同步来的，全系统一份）。
 * 接目录的是少数部署，所以排在最后、默认仍停在部门；没接目录时和原来一样。
 */
import { Tabs } from '../../../ui/Tabs';
import { AdminPage } from '../AdminPage';
import { useOrgDepartments } from '../queries';
import { DepartmentsTab } from './DepartmentsTab';
import { GroupsTab } from './GroupsTab';
import { OrganizationTab } from './OrganizationTab';

export default function OrgPage() {
  const org = useOrgDepartments();
  const synced = (org.data ?? []).length > 0;
  return (
    <AdminPage
      title="部门与用户组"
      description={
        synced
          ? '部门、用户组是本空间自己建的分组；组织架构从企业目录同步来，只能看。授权时可以直接授给它们。'
          : '把成员按部门或临时协作关系分组，授权时可以直接授给部门或用户组。'
      }
    >
      <Tabs
        items={[
          { key: 'departments', label: '部门', children: <DepartmentsTab /> },
          { key: 'groups', label: '用户组', children: <GroupsTab /> },
          ...(synced ? [{ key: 'organization', label: '组织架构', children: <OrganizationTab /> }] : []),
        ]}
      />
    </AdminPage>
  );
}
