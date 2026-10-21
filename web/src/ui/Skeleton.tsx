/** 首屏加载的占位（界面规范 3.6）：几行浅灰条，最后一行短一些。组件库没有，照 ChatGPT 加载列表时的样子。 */
import { cx } from './cx';

export function Skeleton({ rows = 4, className }: { readonly rows?: number; readonly className?: string }) {
  return (
    <div className={cx('kw-skeleton', className)} aria-busy="true" aria-label="加载中">
      {Array.from({ length: rows }, (_, i) => (
        <div
          key={i}
          className="kw-skeleton-line"
          style={i === rows - 1 && rows > 1 ? { width: '60%' } : undefined}
        />
      ))}
    </div>
  );
}
