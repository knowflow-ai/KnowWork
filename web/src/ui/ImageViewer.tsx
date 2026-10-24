/**
 * 看大图：点图后铺满屏幕看，照 ChatGPT 的看图层：暗色遮罩、图居中按屏幕大小缩放、下面一行图注、右上角关闭。
 * Esc、点图外面或关闭按钮都能关。不用跳新标签页：图片地址带短期令牌，新标签页里过期了就打不开，也离开了对话。
 */
import { Dialog as Radix, VisuallyHidden } from 'radix-ui';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { X } from '@openai/apps-sdk-ui/components/Icon';

interface Props {
  readonly src: string;
  readonly alt: string;
  readonly caption?: string;
  readonly onClose: () => void;
}

export function ImageViewer({ src, alt, caption, onClose }: Props) {
  return (
    <Radix.Root open onOpenChange={(open) => (open ? undefined : onClose())}>
      <Radix.Portal>
        <Radix.Overlay className="kw-image-viewer-overlay" />
        <Radix.Content className="kw-image-viewer" aria-describedby={undefined} onClick={onClose}>
          <VisuallyHidden.Root asChild>
            <Radix.Title>{alt}</Radix.Title>
          </VisuallyHidden.Root>
          <Radix.Close asChild>
            <Button
              className="kw-image-viewer-close"
              color="secondary"
              variant="soft"
              size="md"
              uniform
              aria-label="关闭"
            >
              <X />
            </Button>
          </Radix.Close>
          {/* 点图本身不关，免得想细看时一点就没了 */}
          <figure className="kw-image-viewer-figure" onClick={(event) => event.stopPropagation()}>
            <img src={src} alt={alt} />
            {caption ? <figcaption>{caption}</figcaption> : null}
          </figure>
        </Radix.Content>
      </Radix.Portal>
    </Radix.Root>
  );
}
