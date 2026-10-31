/**
 * 个人设置：账号信息、API Key、连接（个人访问令牌的 MCP 服务）、我的用量；扩展注册的标签页排在后面。
 * 网址里的 tab 指定打开哪一页（对话里「管理记忆」这类入口用），切换时跟着改；不认识的打开第一页。
 */
import { useSearchParams } from 'react-router';
import { slotItems } from '../../slots/registry';
import { Tabs } from '../../ui/Tabs';
import { AdminPage } from '../admin/AdminPage';
import { ConnectionsTab } from './ConnectionsTab';
import { AccountTab, ApiKeysTab, MyUsageTab } from './tabs';

export default function SettingsPage() {
  const [params, setParams] = useSearchParams();
  const items = [
    { key: 'account', label: '账号', children: <AccountTab /> },
    { key: 'api-keys', label: 'API Key', children: <ApiKeysTab /> },
    { key: 'connections', label: '连接', children: <ConnectionsTab /> },
    { key: 'usage', label: '我的用量', children: <MyUsageTab /> },
    ...slotItems('settings.tabs'),
  ];
  const value = items.find((item) => item.key === params.get('tab'))?.key ?? 'account';
  return (
    <AdminPage title="个人设置" description="管理你自己的账号、API Key 与外部服务的连接，查看你的用量。">
      <Tabs items={items} value={value} onChange={(tab) => setParams({ tab }, { replace: true })} />
    </AdminPage>
  );
}
