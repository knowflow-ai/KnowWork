/**
 * 对话框：Radix Dialog 加官方令牌（--modal-*）。组件库没有对话框，照 ChatGPT 的弹窗做：白底圆角 16、
 * 右上角关闭、底部操作右对齐。宽度只有 520 / 720 / 1000 三档（界面规范 3.4），更宽改用抽屉（Sheet）。
 */
import type { FormEvent, ReactNode } from 'react';
import { Dialog as Radix } from 'radix-ui';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { X } from '@openai/apps-sdk-ui/components/Icon';
import { cx } from './cx';

const WIDTH = { sm: 520, md: 720, lg: 1000 } as const;

export interface DialogProps {
  readonly title: ReactNode;
  readonly description?: ReactNode;
  readonly size?: keyof typeof WIDTH;
  /** 底部操作，通常是 DialogActions。 */
  readonly footer?: ReactNode;
  /** 设置后正文与底部包在表单里：输入框里回车即提交，DialogActions 的确定按钮变成提交按钮。 */
  readonly onSubmit?: () => void;
  readonly onClose: () => void;
  /** 为 false 时点遮罩不关（只显示一次的密钥、链接，误关就再也看不到了）；Esc 与关闭按钮照常可用。 */
  readonly closeOnOutside?: boolean;
  readonly className?: string;
  readonly children?: ReactNode;
}

export function Dialog({
  title,
  description,
  size = 'sm',
  footer,
  onSubmit,
  onClose,
  closeOnOutside = true,
  className,
  children,
}: DialogProps) {
  const submit = (event: FormEvent) => {
    event.preventDefault();
    onSubmit?.();
  };
  const body = (
    <>
      {children ? <div className="kw-dialog-body">{children}</div> : null}
      {footer ? <div className="kw-dialog-footer">{footer}</div> : null}
    </>
  );
  return (
    <Radix.Root open onOpenChange={(open) => (open ? undefined : onClose())}>
      <Radix.Portal>
        <Radix.Overlay className="kw-overlay" />
        <Radix.Content
          className={cx('kw-dialog', className)}
          style={{ width: `min(${WIDTH[size]}px, calc(100vw - 32px))` }}
          // 没有说明时显式置空，否则 Radix 会警告缺少描述
          {...(description ? {} : { 'aria-describedby': undefined })}
          onPointerDownOutside={closeOnOutside ? undefined : (event) => event.preventDefault()}
        >
          <header className="kw-dialog-head">
            <Radix.Title className="kw-dialog-title">{title}</Radix.Title>
            {description ? (
              <Radix.Description className="kw-dialog-description">{description}</Radix.Description>
            ) : null}
          </header>
          {onSubmit ? (
            <form className="kw-dialog-form" onSubmit={submit} noValidate>
              {body}
            </form>
          ) : (
            body
          )}
          <Radix.Close asChild>
            <Button
              color="secondary"
              variant="ghost"
              size="sm"
              uniform
              className="kw-dialog-close"
              aria-label="关闭"
            >
              <X />
            </Button>
          </Radix.Close>
        </Radix.Content>
      </Radix.Portal>
    </Radix.Root>
  );
}

export interface DialogActionsProps {
  readonly okText: string;
  /** 不传时确定按钮是提交按钮，配合 Dialog 的 onSubmit。 */
  readonly onOk?: () => void;
  readonly onCancel?: () => void;
  readonly cancelText?: string;
  readonly loading?: boolean;
  readonly disabled?: boolean;
  readonly danger?: boolean;
  /** 放在左侧的附加内容，例如「本会话内不再询问」。 */
  readonly extra?: ReactNode;
}

/** 底部操作：取消 · 确定，确定在最右（界面规范 3.4）。 */
export function DialogActions({
  okText,
  onOk,
  onCancel,
  cancelText = '取消',
  loading,
  disabled,
  danger,
  extra,
}: DialogActionsProps) {
  return (
    <>
      {extra ? <div className="kw-dialog-extra">{extra}</div> : null}
      <div className="kw-dialog-actions">
        {onCancel ? (
          <Button color="secondary" variant="outline" onClick={onCancel}>
            {cancelText}
          </Button>
        ) : null}
        <Button
          type={onOk ? 'button' : 'submit'}
          color={danger ? 'danger' : 'primary'}
          loading={loading}
          disabled={disabled}
          onClick={onOk}
        >
          {okText}
        </Button>
      </div>
    </>
  );
}
