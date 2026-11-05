/**
 * 授权里各类资源的图标，和其他地方一致（界面规范第 4 节）：知识库是书（「全部」是多个文件夹），目录是文件夹，
 * 技能是闪电，工具同管理后台菜单，扩展登记的类型用它自己给的。授权列表与选知识库的树共用。
 */
import { Bolt, BookClosed, Folder, Folders, Tools } from '@openai/apps-sdk-ui/components/Icon';
import type { ReactNode } from 'react';
import { slotItems } from '../../../slots/registry';

export function resourceIcon(resource: string): ReactNode {
  const [kind = '', id = ''] = resource.split(':', 2);
  if (kind === 'knowledge_base') return id.endsWith('*') ? <Folders /> : <BookClosed />;
  if (kind === 'knowledge_folder') return <Folder />;
  if (kind === 'skill') return <Bolt />;
  if (kind === 'tool') return <Tools />;
  // 扩展登记的资源类型（插槽 grants.resources）用它自己给的图标
  return slotItems('grants.resources').find((item) => item.type === kind)?.icon ?? null;
}
