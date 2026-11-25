import { Button } from '@openai/apps-sdk-ui/components/Button';
import { Sidebar } from '@openai/apps-sdk-ui/components/Icon';
import { Tooltip } from '@openai/apps-sdk-ui/components/Tooltip';

/** 收起、展开右边那栏打开的文件：图标是左侧栏图标的镜像（照官方右侧栏的收起与展开按钮） */
export function PaneToggleButton({
  expanded,
  count,
  onClick,
}: {
  readonly expanded: boolean;
  readonly count: number;
  readonly onClick: () => void;
}) {
  const label = expanded ? '收起打开的文件' : '展开打开的文件';
  return (
    <Tooltip content={expanded ? label : `${label}（${count} 个）`} compact>
      <Button color="secondary" variant="ghost" size="sm" uniform aria-label={label} onClick={onClick}>
        <Sidebar className="kw-icon-mirror" />
      </Button>
    </Tooltip>
  );
}
