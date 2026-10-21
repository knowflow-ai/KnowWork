/**
 * 抽屉：从右侧滑出的面板（文件预览、运行详情、详情查看）。组件库没有，照 ChatGPT 右侧面板做：
 * 左上角关闭、标题居左，标题右侧放切换类控件与普通按钮（界面规范 1.1 第 6 条）。
 */
import type { CSSProperties, ReactNode } from 'react';
import { Dialog as Radix } from 'radix-ui';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { X } from '@openai/apps-sdk-ui/components/Icon';
import { cx } from './cx';

export interface SheetProps {
  readonly title: ReactNode;
  /** 标题右侧的操作。 */
  readonly extra?: ReactNode;
  /** 面板宽度，数字按像素；窄屏时不超过视口。 */
  readonly width?: number | string;
  readonly onClose: () => void;
  /** 正文不留内边距（PDF、网页预览自己铺满）。 */
  readonly flush?: boolean;
  readonly className?: string;
  readonly children?: ReactNode;
}

export function Sheet({ title, extra, width = 720, onClose, flush, className, children }: SheetProps) {
  const size = typeof width === 'number' ? `${width}px` : width;
  const style: CSSProperties = { width: `min(${size}, 100vw)` };
  return (
    <Radix.Root open onOpenChange={(open) => (open ? undefined : onClose())}>
      <Radix.Portal>
        <Radix.Overlay className="kw-overlay kw-overlay-light" />
        <Radix.Content className={cx('kw-sheet', className)} style={style} aria-describedby={undefined}>
          <header className="kw-sheet-head">
            <Radix.Close asChild>
              <Button color="secondary" variant="ghost" size="sm" uniform aria-label="关闭">
                <X />
              </Button>
            </Radix.Close>
            <Radix.Title className="kw-sheet-title">{title}</Radix.Title>
            {extra ? <div className="kw-sheet-extra">{extra}</div> : null}
          </header>
          <div className={cx('kw-sheet-body', flush && 'is-flush')}>{children}</div>
        </Radix.Content>
      </Radix.Portal>
    </Radix.Root>
  );
}
