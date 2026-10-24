/**
 * 授权里各类资源的图标，和其他地方一致（界面规范第 4 节）：知识库是书（「全部」是多个文件夹），目录是文件夹，
 * 技能是闪电，工具同管理后台菜单。授权列表与选知识库的树共用。
 */
import { Bolt, BookClosed, Folder, Folders, Tools } from '@openai/apps-sdk-ui/components/Icon';
import type { ReactNode } from 'react';

export function resourceIcon(resource: string): ReactNode {
  const [kind = '', id = ''] = resource.split(':', 2);
  if (kind === 'knowledge_base') return id.endsWith('*') ? <Folders /> : <BookClosed />;
  if (kind === 'knowledge_folder') return <Folder />;
  if (kind === 'skill') return <Bolt />;
  if (kind === 'tool') return <Tools />;
  return null;
}
