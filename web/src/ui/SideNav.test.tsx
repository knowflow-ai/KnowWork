import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Breadcrumbs, SideNav, SideNavLayout, SideNavToggle } from './SideNav';
import { useSideNavOpen } from './useSideNavOpen';
import { Tree } from './Tree';

afterEach(() => window.localStorage.clear());

function Page() {
  const [open, setOpen] = useSideNavOpen('demo');
  return (
    <SideNavLayout
      open={open}
      nav={
        <SideNav label="目录栏" title="知识库" onCollapse={() => setOpen(false)}>
          <p>树</p>
        </SideNav>
      }
    >
      {open ? null : <SideNavToggle label="目录栏" onOpen={() => setOpen(true)} />}
      <p>内容</p>
    </SideNavLayout>
  );
}

describe('二级侧栏', () => {
  it('栏顶写标题，能收起；收起后用内容区的按钮再展开，开没开记在浏览器里', async () => {
    const user = userEvent.setup();
    const { unmount } = render(<Page />);
    const nav = screen.getByRole('navigation', { name: '目录栏' });
    expect(within(nav).getByText('知识库')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '收起目录栏' }));
    expect(screen.queryByRole('navigation', { name: '目录栏' })).not.toBeInTheDocument();
    expect(screen.getByText('内容')).toBeInTheDocument();
    unmount();
    // 再进来还是收起的
    render(<Page />);
    expect(screen.queryByRole('navigation', { name: '目录栏' })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '展开目录栏' }));
    expect(screen.getByRole('navigation', { name: '目录栏' })).toBeInTheDocument();
  });

  it('路径前面几层能点，最后一层是当前位置', async () => {
    const user = userEvent.setup();
    const back = vi.fn();
    render(
      <Breadcrumbs
        items={[
          { key: 'root', label: '全部知识库', onClick: back },
          { key: 'f1', label: '研发中心' },
        ]}
      />,
    );
    const path = screen.getByRole('navigation', { name: '位置' });
    expect(path).toHaveTextContent('全部知识库/研发中心');
    expect(within(path).getByText('研发中心')).toHaveAttribute('aria-current', 'page');
    await user.click(within(path).getByRole('button', { name: '全部知识库' }));
    expect(back).toHaveBeenCalled();
  });
});

describe('导航树（Tree variant="nav"）', () => {
  it('分组画成组标题（点了收起展开），组里的项不缩进；全是叶子时不留三角的空位；行尾放操作', async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(
      <Tree
        variant="nav"
        aria-label="语义模型"
        onSelect={onSelect}
        selected="ds:orders"
        nodes={[
          { key: 'overview', title: '总览', children: [] },
          {
            key: 'grp:tables',
            title: '表',
            selectable: false,
            children: [
              {
                key: 'ds:orders',
                title: '订单',
                children: [],
                actions: <button type="button">订单的操作</button>,
              },
            ],
          },
        ]}
      />,
    );
    const tree = screen.getByRole('tree', { name: '语义模型' });
    expect(tree).toHaveClass('is-nav');
    expect(within(tree).queryByRole('button', { name: '展开' })).not.toBeInTheDocument();
    expect(within(tree).queryByRole('button', { name: '收起' })).not.toBeInTheDocument();
    const orders = within(tree).getByRole('button', { name: '订单' }).closest('.kw-tree-row') as HTMLElement;
    expect(orders.style.paddingLeft).toBe('4px'); // 组里的项不缩进
    expect(orders).toHaveClass('is-selected');
    expect(within(orders).getByRole('button', { name: '订单的操作' })).toBeInTheDocument();
    const heading = within(tree).getByRole('button', { name: '表' }).closest('.kw-tree-row');
    expect(heading).toHaveClass('is-heading');

    await user.click(within(tree).getByRole('button', { name: '表' }));
    expect(within(tree).queryByRole('button', { name: '订单' })).not.toBeInTheDocument();
    expect(onSelect).not.toHaveBeenCalled();
    await user.click(within(tree).getByRole('button', { name: '总览' }));
    expect(onSelect).toHaveBeenCalledWith('overview');
  });
});
