/** 空状态与整页的结果提示（界面规范 3.6）：组件库的 EmptyMessage，图标可选，下方可放操作。 */
import type { ReactNode } from 'react';
import { EmptyMessage } from '@openai/apps-sdk-ui/components/EmptyMessage';

interface Props {
  readonly title?: ReactNode;
  readonly description?: ReactNode;
  readonly icon?: ReactNode;
  readonly tone?: 'secondary' | 'danger' | 'warning';
  readonly action?: ReactNode;
  readonly className?: string;
}

export function Empty({ title, description, icon, tone = 'secondary', action, className }: Props) {
  return (
    <EmptyMessage className={className}>
      {icon ? <EmptyMessage.Icon color={tone}>{icon}</EmptyMessage.Icon> : null}
      {title ? (
        <EmptyMessage.Title color={tone === 'secondary' ? undefined : tone}>{title}</EmptyMessage.Title>
      ) : null}
      {description ? <EmptyMessage.Description>{description}</EmptyMessage.Description> : null}
      {action ? <EmptyMessage.ActionRow>{action}</EmptyMessage.ActionRow> : null}
    </EmptyMessage>
  );
}
