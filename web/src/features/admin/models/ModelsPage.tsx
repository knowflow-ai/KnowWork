/** 模型：厂商、模型、分组与连通测试。 */
import { Tabs } from '../../../ui/Tabs';
import { AdminPage } from '../AdminPage';
import { GroupsTab, ModelsTab, ProvidersTab } from './tabs';

export default function ModelsPage() {
  return (
    <AdminPage
      title="模型"
      description="接入模型厂商、登记可用的模型，再按分组授权给成员。密钥保存后不再显示明文。"
    >
      <Tabs
        items={[
          { key: 'models', label: '模型', children: <ModelsTab /> },
          { key: 'providers', label: '厂商', children: <ProvidersTab /> },
          { key: 'groups', label: '分组', children: <GroupsTab /> },
        ]}
      />
    </AdminPage>
  );
}
