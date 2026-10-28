/**
 * 二次确认（界面规范 3.5）：
 * - confirm()（confirmStore.ts）：对话框确认，重操作和收进「更多」里的删除用它，返回用户是否确认；
 * - ConfirmPopover：行内气泡确认，表格行里的删除用它。
 * ConfirmHost 挂在 Providers 里，同一时间只有一个确认框。
 */
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { Popover } from '@openai/apps-sdk-ui/components/Popover';
import { Dialog, DialogActions } from './Dialog';
import { useConfirmState, type ConfirmOptions } from './confirmStore';

export function ConfirmHost() {
  const pending = useConfirmState((state) => state.pending);
  if (!pending) return null;
  const settle = (ok: boolean) => {
    useConfirmState.setState({ pending: null });
    pending.resolve(ok);
  };
  return (
    <Dialog
      title={pending.title}
      description={pending.description}
      onClose={() => settle(false)}
      footer={
        <DialogActions
          okText={pending.okText}
          cancelText={pending.cancelText}
          danger={pending.danger}
          onCancel={() => settle(false)}
          onOk={() => settle(true)}
        />
      }
    />
  );
}

interface PopoverProps extends ConfirmOptions {
  readonly onConfirm: () => void;
  /** 触发按钮，必须是能接收 ref 的元素（Button）。 */
  readonly children: ReactNode;
}

/**
 * 气泡里的内容。组件库的 Popover 打开时会立刻把焦点移进浮层，可浮层在 portal 里，若外面是对话框或抽屉，
 * 对话框的焦点陷阱这时还没让位，会把焦点拉回去，气泡随即关闭（2026-10-20 部门页的抽屉里点「移出」没反应）。
 * 所以关掉它的自动聚焦，等浮层挂好后再把焦点放到「取消」上，键盘用户照样能操作。
 */
function ConfirmContent({
  title,
  description,
  okText,
  danger,
  onCancel,
  onOk,
}: ConfirmOptions & { readonly onCancel: () => void; readonly onOk: () => void }) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const timer = setTimeout(() => box.current?.querySelector('button')?.focus());
    return () => clearTimeout(timer);
  }, []);
  return (
    <div
      ref={box}
      className="kw-confirm-pop"
      role="alertdialog"
      aria-label={typeof title === 'string' ? title : undefined}
    >
      <div className="kw-confirm-title">{title}</div>
      {description ? <div className="kw-confirm-description">{description}</div> : null}
      <div className="kw-confirm-actions">
        <Button color="secondary" variant="outline" size="sm" onClick={onCancel}>
          取消
        </Button>
        <Button color={danger ? 'danger' : 'primary'} size="sm" onClick={onOk}>
          {okText}
        </Button>
      </div>
    </div>
  );
}

export function ConfirmPopover({ title, description, okText, danger, onConfirm, children }: PopoverProps) {
  const [open, setOpen] = useState(false);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <Popover.Trigger>{children}</Popover.Trigger>
      <Popover.Content width={280} align="end" side="top" autoFocus={false}>
        <ConfirmContent
          title={title}
          description={description}
          okText={okText}
          danger={danger}
          onCancel={() => setOpen(false)}
          onOk={() => {
            setOpen(false);
            onConfirm();
          }}
        />
      </Popover.Content>
    </Popover>
  );
}
