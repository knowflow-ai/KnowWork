/**
 * 品牌标识：侧栏顶部、管理后台顶部、登录页用。
 * - 默认品牌：叶子 K 直接当「Know」的首字母，后接「now」深色、「Work」蓝色，读出来就是 KnowWork；
 *   K 和 Work 同一个晴空蓝，K 不再分三种深浅（浅色叶片在侧栏的浅灰底上几乎看不见）；
 *   图标后面再写一遍 KnowWork 会读成「K KnowWork」（2026-10-21 用户反馈）。
 * - 改过名称或 Logo：自定义 Logo（没传就用方形的叶子 K）加名称。
 * 字样用网页文字而不是转成路径的图片：二十来像素的字转成路径没有字体微调，1 倍屏上发虚。
 */
import { DEFAULT_BRAND, DEFAULT_LETTER_K, DEFAULT_MARK } from '../../api/system';
import { cx } from '../../ui/cx';
import { useBrand } from './queries';

export function BrandMark({ size = 24, className }: { readonly size?: number; readonly className?: string }) {
  const brand = useBrand();
  const isDefault = brand.logoUrl === null && brand.name === DEFAULT_BRAND.name;
  if (isDefault) {
    return (
      <span
        className={cx('kw-brand', 'kw-wordmark', className)}
        role="img"
        aria-label={brand.name}
        style={{ fontSize: Math.round(size * 0.8) }}
      >
        <img className="kw-wordmark-k" src={DEFAULT_LETTER_K} alt="" />
        <span aria-hidden="true">
          now<span className="kw-brand-accent">Work</span>
        </span>
      </span>
    );
  }
  return (
    <span className={cx('kw-brand', className)} style={{ gap: Math.round(size / 4) }}>
      <img
        className="kw-brand-mark"
        src={brand.logoUrl ?? DEFAULT_MARK}
        alt=""
        style={{ height: size, maxWidth: size * 4 }}
      />
      <span className="kw-brand-name" style={{ fontSize: Math.round(size * 0.75) }}>
        {brand.name}
      </span>
    </span>
  );
}
