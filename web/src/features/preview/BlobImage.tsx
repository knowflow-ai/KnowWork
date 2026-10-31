/**
 * 把取到的字节显示成图：地址在 ref 回调里建、在它的清理里释放，换内容或卸载时释放。
 * 不在渲染时建（useMemo）：开发时 StrictMode 挂上就模拟卸载一次，那次清理会把地址释放掉，
 * 重新挂上却还拿着旧地址，预览抽屉里的图就裂了。
 */
import { useCallback } from 'react';

export function BlobImage({
  data,
  alt,
  className,
}: {
  readonly data: ArrayBuffer;
  readonly alt: string;
  readonly className?: string;
}) {
  const attach = useCallback(
    (img: HTMLImageElement | null) => {
      if (!img) return undefined;
      const url = URL.createObjectURL(new Blob([data]));
      img.src = url;
      return () => URL.revokeObjectURL(url);
    },
    [data],
  );
  return <img ref={attach} className={className} alt={alt} />;
}
