/** 管理页表格的操作列：文字按钮、最多露 3 个，多余的收进「更多」，删除类先确认（界面规范 3.3、3.5）。 */
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { ChevronDown } from '@openai/apps-sdk-ui/components/Icon';
import { Menu } from '@openai/apps-sdk-ui/components/Menu';
import { ConfirmPopover } from '../../ui/confirm';
import { confirm } from '../../ui/confirmStore';

export interface RowAction {
  readonly key: string;
  readonly label: string;
  readonly onClick?: () => void;
  readonly danger?: boolean;
  /** 设置时点了先弹行内确认。 */
  readonly confirm?: string;
  readonly disabled?: boolean;
}

const VISIBLE = 3;

function ActionButton({ action }: { readonly action: RowAction }) {
  const button = (
    <Button
      color={action.danger ? 'danger' : 'secondary'}
      variant="ghost"
      size="sm"
      disabled={action.disabled}
      onClick={action.confirm ? undefined : action.onClick}
    >
      {action.label}
    </Button>
  );
  if (!action.confirm || action.disabled) return button;
  return (
    <ConfirmPopover
      title={action.confirm}
      okText={action.label}
      danger={action.danger}
      onConfirm={() => action.onClick?.()}
    >
      {button}
    </ConfirmPopover>
  );
}

/** 操作列：最多露 3 个，其余收进「更多」。收进去的删除改用对话框确认。 */
export function RowActions({ actions }: { readonly actions: readonly RowAction[] }) {
  const shown = actions.length > VISIBLE ? actions.slice(0, VISIBLE - 1) : actions;
  const rest = actions.length > VISIBLE ? actions.slice(VISIBLE - 1) : [];
  const runHidden = async (action: RowAction) => {
    if (
      action.confirm &&
      !(await confirm({ title: action.confirm, okText: action.label, danger: action.danger }))
    )
      return;
    action.onClick?.();
  };
  return (
    <span className="kw-row-actions">
      {shown.map((action) => (
        <ActionButton key={action.key} action={action} />
      ))}
      {rest.length > 0 ? (
        <Menu>
          <Menu.Trigger>
            <Button color="secondary" variant="ghost" size="sm">
              更多
              <ChevronDown />
            </Button>
          </Menu.Trigger>
          <Menu.Content align="end" minWidth={140}>
            {rest.map((action) => (
              <Menu.Item
                key={action.key}
                disabled={action.disabled}
                className={action.danger ? 'kw-menu-danger' : undefined}
                onSelect={() => void runHidden(action)}
              >
                {action.label}
              </Menu.Item>
            ))}
          </Menu.Content>
        </Menu>
      ) : null}
    </span>
  );
}
