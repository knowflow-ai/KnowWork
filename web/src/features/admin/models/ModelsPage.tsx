/** 模型：厂商、模型与连通测试，以及文档解析服务（MinerU）；扩展可以经插槽 admin.models.tabs 加自己的服务配置。
 * 模型对空间成员一律可用，不按人授权。 */
import { slotItems } from '../../../slots/registry';
import { Tabs } from '../../../ui/Tabs';
import { AdminPage } from '../AdminPage';
import { ParserTab } from './ParserTab';
import { ModelsTab, ProvidersTab } from './tabs';

export default function ModelsPage() {
  return (
    <AdminPage
      title="模型"
      description="接入模型厂商、登记可用的模型，空间成员都能用；文档解析服务也在这里配。密钥保存后不再显示明文。"
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
