/** 模型：厂商、模型与连通测试，以及文档解析服务（MinerU）；扩展可以经插槽 admin.models.tabs 加自己的服务配置。
 * 全平台共用一份，只有系统管理员能改；所有空间的成员都能用，不按人授权。 */
import { Lock } from '@openai/apps-sdk-ui/components/Icon';
import { slotItems } from '../../../slots/registry';
import { Empty } from '../../../ui/Empty';
import { Skeleton } from '../../../ui/Skeleton';
import { Tabs } from '../../../ui/Tabs';
import { useIsSystemAdmin } from '../../brand/queries';
import { AdminPage } from '../AdminPage';
import { ParserTab } from './ParserTab';
import { ModelsTab, ProvidersTab } from './tabs';

export default function ModelsPage() {
  const systemAdmin = useIsSystemAdmin();
  if (systemAdmin.isPending) return <Skeleton rows={6} className="kw-page-state" />;
  if (!systemAdmin.data) {
    return (
      <Empty
        area
        icon={<Lock />}
        title="没有权限"
        description="模型与文档解析是所有空间共用的，只有系统管理员（初始化时创建的账号）能配置"
      />
    );
  }
  return (
    <AdminPage
      title="模型"
      description="接入模型厂商、登记可用的模型，所有空间的成员都能用；文档解析服务也在这里配。密钥保存后不再显示明文。"
    >
      <Tabs
        items={[
          { key: 'models', label: '模型', children: <ModelsTab /> },
          { key: 'providers', label: '厂商', children: <ProvidersTab /> },
          { key: 'parser', label: '文档解析', children: <ParserTab /> },
          ...slotItems('admin.models.tabs'),
        ]}
      />
    </AdminPage>
  );
}
