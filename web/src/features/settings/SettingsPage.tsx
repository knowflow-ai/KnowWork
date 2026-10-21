/** 个人设置：账号信息、API Key、我的用量。 */
import { Tabs } from '../../ui/Tabs';
import { AdminPage } from '../admin/AdminPage';
import { AccountTab, ApiKeysTab, MyUsageTab } from './tabs';

export default function SettingsPage() {
  return (
    <AdminPage title="个人设置" description="管理你自己的账号与 API Key，查看你的用量。">
      <Tabs
        items={[
          { key: 'account', label: '账号', children: <AccountTab /> },
          { key: 'api-keys', label: 'API Key', children: <ApiKeysTab /> },
          { key: 'usage', label: '我的用量', children: <MyUsageTab /> },
        ]}
      />
    </AdminPage>
  );
}
