/**
 * 页面里的二级侧栏（知识库首页的目录、指标页的语义模型）：从页面顶上一直到底单独一栏，照 ChatGPT 设置弹窗的左列——
 * 栏顶一行是标题（或切换器）和几个图标按钮，下面是导航（一般是 variant="nav" 的 Tree）；右边内容区有自己的路径、标题和按钮。
 * 原来树挂在页面标题和工具条下面，搜索框、按钮横跨两栏，看不出管的是哪一栏（2026-11-24 走查，用户看过设计稿定的）。
 * 能收起：收起后内容区占满，内容区顶上放 SideNavToggle 再打开；开没开用 useSideNavOpen 记。
 */
import type { ReactNode } from 'react';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { Sidebar } from '@openai/apps-sdk-ui/components/Icon';
import { Tooltip } from '@openai/apps-sdk-ui/components/Tooltip';
import { cx } from './cx';

/** 栏顶、内容区顶上的图标按钮：带悬停提示，读屏念 label */
export function SideNavButton({
  label,
  onClick,
  children,
  disabled,
}: {
  readonly label: string;
  readonly onClick?: () => void;
  readonly children: ReactNode;
  readonly disabled?: boolean;
}) {
  return (
    <Tooltip content={label} compact>
      <Button
        color="secondary"
        variant="ghost"
        size="sm"
        uniform
        aria-label={label}
        disabled={disabled}
        onClick={onClick}
      >
        {children}
      </Button>
    </Tooltip>
  );
}

export function SideNavLayout({
  open,
  nav,
  children,
  className,
}: {
  readonly open: boolean;
  /** 一个 SideNav；收起时不渲染 */
  readonly nav: ReactNode;
  readonly children: ReactNode;
  readonly className?: string;
}) {
  return (
    <div className={cx('kw-sidenav-layout', !open && 'is-collapsed', className)}>
      {open ? nav : null}
      <div className="kw-sidenav-main">{children}</div>
    </div>
  );
}

/**
 * 二级侧栏本身：label 是这一栏的名字（「目录栏」「模型栏」），读屏念它，收起按钮叫「收起{label}」。
 * actions 放在标题右边、收起按钮前面（新建目录、更多……）。
 */
export function SideNav({
  label,
  title,
  actions,
  onCollapse,
  children,
}: {
  readonly label: string;
  readonly title: ReactNode;
  readonly actions?: ReactNode;
  readonly onCollapse: () => void;
  readonly children: ReactNode;
}) {
  return (
    <nav className="kw-sidenav" aria-label={label}>
      <div className="kw-sidenav-head">
        <div className="kw-sidenav-title">{title}</div>
        <div className="kw-sidenav-tools">
          {actions}
          <SideNavButton label={`收起${label}`} onClick={onCollapse}>
            <Sidebar />
          </SideNavButton>
        </div>
      </div>
      {children}
    </nav>
  );
}

/** 收起后放在内容区顶上、把二级侧栏再打开的按钮 */
export function SideNavToggle({ label, onOpen }: { readonly label: string; readonly onOpen: () => void }) {
  return (
    <SideNavButton label={`展开${label}`} onClick={onOpen}>
      <Sidebar />
    </SideNavButton>
  );
}

/** 内容区顶上一行：左边路径（收起时前面多一个展开按钮），右边这一页的按钮；高度和二级侧栏的栏顶一样，两边对齐 */
export function SideNavBar({ left, right }: { readonly left: ReactNode; readonly right?: ReactNode }) {
  return (
    <div className="kw-sidenav-bar">
      <div className="kw-sidenav-bar-left">{left}</div>
      {right ? <div className="kw-sidenav-bar-right">{right}</div> : null}
    </div>
  );
}

export interface Crumb {
  readonly key: string;
  readonly label: string;
  /** 没有就是当前这一层（最后一项），只写字不能点 */
  readonly onClick?: () => void;
}

/** 路径：「全部知识库 / 研发中心 / 产品线 A」，前面几层能点回去 */
export function Breadcrumbs({ items }: { readonly items: readonly Crumb[] }) {
  return (
    <nav className="kw-crumbs" aria-label="位置">
      {items.map((item, index) => (
        <span key={item.key} className="kw-crumbs-item">
          {index > 0 ? <span aria-hidden="true">/</span> : null}
          {item.onClick ? (
            <button type="button" className="kw-crumbs-link" onClick={item.onClick}>
              {item.label}
            </button>
          ) : (
            <span aria-current="page">{item.label}</span>
          )}
        </span>
      ))}
    </nav>
  );
}
