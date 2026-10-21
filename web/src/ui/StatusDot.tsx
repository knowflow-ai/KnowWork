/**
 * 状态：圆点加文字。只在管理后台的表格里用（界面规范 3.3），使用端的状态只写文字不上色。
 * 同一含义全站同一颜色：正常绿、异常红、注意橙、进行中蓝、停用或未知灰。
 */
import type { ReactNode } from 'react';

export type Tone = 'success' | 'error' | 'warning' | 'processing' | 'default';

export function StatusDot({ tone, children }: { readonly tone: Tone; readonly children: ReactNode }) {
  return (
    <span className="kw-status">
      <span className={`kw-status-dot is-${tone}`} aria-hidden="true" />
      {children}
    </span>
  );
}
