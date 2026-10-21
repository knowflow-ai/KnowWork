/**
 * 表单项：标签、控件、说明、错误。组件库没有表单，按 ChatGPT 设置页的写法：标签在上、说明色小字在下，
 * 出错时说明换成错误原因。
 *
 * 子元素传函数时拿到 id，用 <label for> 关联输入框；传节点时（选择框、开关组这类按钮式控件）整块是一个
 * 以标签命名的 group，测试和读屏都按标签找到它。
 */
import { useId, type ReactNode } from 'react';
import { cx } from './cx';

interface Props {
  readonly label?: ReactNode;
  readonly help?: ReactNode;
  readonly error?: ReactNode;
  readonly required?: boolean;
  readonly className?: string;
  readonly children: ReactNode | ((id: string) => ReactNode);
}

export function Field({ label, help, error, required, className, children }: Props) {
  const id = useId();
  const labelId = `${id}-label`;
  const noteId = `${id}-note`;
  const note = error ?? help;
  const byId = typeof children === 'function';
  return (
    <div
      className={cx('kw-field', error ? 'is-invalid' : undefined, className)}
      role={byId || !label ? undefined : 'group'}
      aria-labelledby={byId || !label ? undefined : labelId}
    >
      {label ? (
        <label id={labelId} className="kw-field-label" htmlFor={byId ? id : undefined}>
          {label}
          {required ? (
            <span className="kw-field-required" aria-hidden="true">
              {' '}
              *
            </span>
          ) : null}
        </label>
      ) : null}
      {byId ? children(id) : children}
      {note ? (
        <div id={noteId} className="kw-field-note" role={error ? 'alert' : undefined}>
          {note}
        </div>
      ) : null}
    </div>
  );
}
