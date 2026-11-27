/**
 * 知识库的图标：圆角底块里的深色书本（2026-11-26 用户看对比稿定的方案 A）。目录是不带底块的线条文件夹；原来知识库也是
 * 不带底块的线条书本，两个都是灰色、同样大小，缩小以后轮廓接近，一眼分不出哪个是目录、哪个是知识库。带底块的是「一个
 * 东西」，不带的是「装东西的」，靠有没有底块分，不靠细看轮廓。
 *
 * 目录与知识库会出现在一起的地方都用它（知识库首页列表、输入框 @ 菜单与已选标签、授权、选知识库的树、专家详情）；
 * 主导航上的「知识库」不用，和其他导航项一样是线条图标。@ 菜单里每种类型本来就有浅灰底块，知识库那一格换成深一档的
 * 底块、图标用正文色（样式在 ui.css）。
 */
import { BookClosed } from '@openai/apps-sdk-ui/components/Icon';
import { cx } from './cx';

export function KnowledgeBaseIcon({ className }: { readonly className?: string }) {
  return (
    <span className={cx('kw-kb-icon', className)} aria-hidden>
      <BookClosed />
    </span>
  );
}
