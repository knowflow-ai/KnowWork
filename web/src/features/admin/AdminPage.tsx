/** 管理页外壳：页头标题 20 + 一句说明，内容直接铺在白底上（界面规范第 3 节）。 */
import type { ReactNode } from 'react';
import { cx } from '../../ui/cx';

interface Props {
  readonly title: string;
  readonly description: string;
  readonly children: ReactNode;
  /** 页面自己的布局类名，例如要让内容撑满页头下面剩下的高度 */
  readonly className?: string;
}

export function AdminPage({ title, description, children, className }: Props) {
  return (
    <div className={cx('kw-admin', className)}>
      <header className="kw-admin-head">
        <h1>{title}</h1>
        <p>{description}</p>
      </header>
      {children}
    </div>
  );
}

/** 页头下方工具条：左侧搜索或筛选，右侧主操作。 */
export function Toolbar({ left, right }: { readonly left?: ReactNode; readonly right?: ReactNode }) {
  return (
    <div className="kw-admin-toolbar">
      <div className="kw-admin-toolbar-left">{left}</div>
      <div className="kw-admin-toolbar-right">{right}</div>
    </div>
  );
}
