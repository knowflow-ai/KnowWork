/** 管理页外壳：页头标题 20 + 一句说明，内容直接铺在白底上（界面规范第 3 节）。 */
import type { ReactNode } from 'react';

interface Props {
  readonly title: string;
  readonly description: string;
  readonly children: ReactNode;
}

export function AdminPage({ title, description, children }: Props) {
  return (
    <div className="kw-admin">
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
