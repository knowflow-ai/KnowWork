/** 部门与用户组。 */
import { Tabs } from '../../../ui/Tabs';
import { AdminPage } from '../AdminPage';
import { DepartmentsTab } from './DepartmentsTab';
import { GroupsTab } from './GroupsTab';

export default function OrgPage() {
  return (
    <AdminPage
      title="部门与用户组"
      description="按组织架构或临时协作关系把成员分组，授权时可以直接授给部门或用户组。"
    >
      <Tabs
        items={[
          { key: 'departments', label: '部门', children: <DepartmentsTab /> },
          { key: 'groups', label: '用户组', children: <GroupsTab /> },
        ]}
      />
    </AdminPage>
  );
}
