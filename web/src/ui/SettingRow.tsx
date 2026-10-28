/**
 * 设置行：照 ChatGPT 设置页，左边标题与一句说明，右边控件（开关、短输入框、选择框、按钮）；几行放进 SettingList，
 * 行间一道细线。比 Field 适合「打开某个功能、调一两个参数」这类设置，标题和控件在同一行，扫一眼就知道开没开。
 *
 * 子元素传函数时拿到 id，标题用 <label for> 关联开关或输入框；传节点时（选择框、按钮）整行是一个以标题命名的 group，
 * 测试和读屏都按标题找到它（同 Field）。
 */
import { useId, type ReactNode } from 'react';
import { cx } from './cx';

interface RowProps {
  readonly title: ReactNode;
  readonly description?: ReactNode;
  readonly children: ReactNode | ((id: string) => ReactNode);
}

export function SettingRow({ title, description, children }: RowProps) {
  const id = useId();
  const titleId = `${id}-title`;
  const descriptionId = `${id}-description`;
  const byId = typeof children === 'function';
  return (
    <div
      className="kw-setting-row"
      role={byId ? undefined : 'group'}
      aria-labelledby={byId ? undefined : titleId}
      aria-describedby={description ? descriptionId : undefined}
    >
      <div className="kw-setting-row-text">
        {byId ? (
          <label id={titleId} htmlFor={id} className="kw-setting-row-title">
            {title}
          </label>
        ) : (
          <div id={titleId} className="kw-setting-row-title">
            {title}
          </div>
        )}
        {description ? (
          <p id={descriptionId} className="kw-setting-row-description">
            {description}
          </p>
        ) : null}
      </div>
      <div className="kw-setting-row-control">{byId ? children(id) : children}</div>
    </div>
  );
}

export function SettingList({
  children,
  className,
}: {
  readonly children: ReactNode;
  readonly className?: string;
}) {
  return <div className={cx('kw-setting-list', className)}>{children}</div>;
}
