/**
 * 「技能」页（使用端，从侧栏主菜单进来：技能是拿来做事的，不算个人设置）：
 * 「可以用的」列出对话里能用的全部技能（空间、自带、我的）；「我的技能」管理自己上传的，可以提交到空间。
 * 网址里的 tab 指定打开哪一页，切换时跟着改；不认识的打开第一页。
 */
import { useSearchParams } from 'react-router';
import { Tabs } from '../../ui/Tabs';
import { AdminPage } from '../admin/AdminPage';
import { AvailableTab } from './AvailableTab';
import { MySkillsTab } from './MySkillsTab';

export default function SkillsPage() {
  const [params, setParams] = useSearchParams();
  const items = [
    { key: 'available', label: '可以用的', children: <AvailableTab /> },
    { key: 'mine', label: '我的技能', children: <MySkillsTab /> },
  ];
  const value = items.find((item) => item.key === params.get('tab'))?.key ?? 'available';
  return (
    <AdminPage
      title="技能"
      description="技能是写好的做事方法。对话里打 @ 可以点名用哪个，不点名时智能体也会按需要自己用上。"
    >
      <Tabs items={items} value={value} onChange={(tab) => setParams({ tab }, { replace: true })} />
    </AdminPage>
  );
}
