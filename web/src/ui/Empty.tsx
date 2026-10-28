/**
 * 空状态与整页的结果提示（界面规范 3.6）：组件库的 EmptyMessage，图标可选，下方可放操作。
 * area：整页、整个标签页、整栏面板、预览区这类没有固定高度的区域里用，空状态落在空白的中间（EmptyMessage 本来
 * 撑满区域、上下居中，没有高度的容器里会贴在顶部）；配一个图标。对话框、抽屉、区块里的一小块不用。
 */
import type { ReactNode } from 'react';
import { EmptyMessage } from '@openai/apps-sdk-ui/components/EmptyMessage';
import { cx } from './cx';

interface Props {
  readonly title?: ReactNode;
  readonly description?: ReactNode;
  readonly icon?: ReactNode;
  readonly tone?: 'secondary' | 'danger' | 'warning';
  readonly action?: ReactNode;
  readonly area?: boolean;
  readonly className?: string;
}

export function Empty({ title, description, icon, tone = 'secondary', action, area, className }: Props) {
  return (
    <EmptyMessage className={cx(area && 'kw-area-state', className)}>
      {icon ? <EmptyMessage.Icon color={tone}>{icon}</EmptyMessage.Icon> : null}
      {title ? (
        <EmptyMessage.Title color={tone === 'secondary' ? undefined : tone}>{title}</EmptyMessage.Title>
      ) : null}
      {description ? <EmptyMessage.Description>{description}</EmptyMessage.Description> : null}
      {action ? <EmptyMessage.ActionRow>{action}</EmptyMessage.ActionRow> : null}
    </EmptyMessage>
  );
}
