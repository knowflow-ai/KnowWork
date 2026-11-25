import { describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithApp } from '../test/render';
import { DocTabs } from './DocTabs';

const TABS = [
  { id: 'a', title: '合同.docx', hint: 'out/合同.docx' },
  { id: 'b', title: '预算.xlsx' },
  { id: 'c', title: '汇报.pptx' },
];

function setup(activeId = 'b') {
  const onSelect = vi.fn();
  const onClose = vi.fn();
  renderWithApp(
    <DocTabs
      label="打开的文件"
      tabs={TABS}
      activeId={activeId}
      onSelect={onSelect}
      onClose={onClose}
      end={<span>操作</span>}
    />,
  );
  return { onSelect, onClose, strip: screen.getByRole('tablist', { name: '打开的文件' }) };
}

describe('DocTabs（照官方右侧栏的标签条）', () => {
  it('点标签切换、点标签上的关闭只关它；当前标签可以 Tab 键进来，悬停说明是完整位置；右端放调用方的控件', async () => {
    const { onSelect, onClose, strip } = setup();
    const tabs = within(strip).getAllByRole('tab');
    expect(tabs.map((tab) => tab.getAttribute('aria-selected'))).toEqual(['false', 'true', 'false']);
    expect(tabs.map((tab) => tab.tabIndex)).toEqual([-1, 0, -1]);
    expect(tabs[0]).toHaveAttribute('title', 'out/合同.docx');
    await userEvent.click(tabs[2]!);
    expect(onSelect).toHaveBeenCalledWith('c');
    await userEvent.click(screen.getByRole('button', { name: '关闭 合同.docx' }));
    expect(onClose).toHaveBeenCalledWith(['a']);
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(screen.getByText('操作')).toBeInTheDocument();
  });

  it('中键点标签是关闭，不选中', () => {
    const { onSelect, onClose, strip } = setup();
    fireEvent(
      within(strip).getAllByRole('tab')[0]!,
      new MouseEvent('auxclick', { bubbles: true, button: 1 }),
    );
    expect(onClose).toHaveBeenCalledWith(['a']);
    expect(onSelect).not.toHaveBeenCalled();
  });

  it('右键：关闭、关闭其他、关闭右侧；最右边那个没有右侧可关', async () => {
    const { onClose, strip } = setup();
    fireEvent.contextMenu(within(strip).getAllByRole('tab')[1]!);
    await userEvent.click(await screen.findByRole('menuitem', { name: '关闭其他' }));
    expect(onClose).toHaveBeenLastCalledWith(['a', 'c']);
    fireEvent.contextMenu(within(strip).getAllByRole('tab')[0]!);
    await userEvent.click(await screen.findByRole('menuitem', { name: '关闭右侧' }));
    expect(onClose).toHaveBeenLastCalledWith(['b', 'c']);
    fireEvent.contextMenu(within(strip).getAllByRole('tab')[2]!);
    expect(await screen.findByRole('menuitem', { name: '关闭右侧' })).toHaveAttribute('data-disabled');
  });

  it('键盘：左右键（到头转回来）、Home、End 移焦点，Enter 选中，Delete 关掉焦点所在的', async () => {
    const { onSelect, onClose, strip } = setup();
    const tabs = within(strip).getAllByRole('tab');
    tabs[1]!.focus();
    await userEvent.keyboard('{ArrowRight}');
    expect(tabs[2]).toHaveFocus();
    await userEvent.keyboard('{ArrowRight}');
    expect(tabs[0]).toHaveFocus();
    await userEvent.keyboard('{End}');
    expect(tabs[2]).toHaveFocus();
    await userEvent.keyboard('{Enter}');
    expect(onSelect).toHaveBeenCalledWith('c');
    await userEvent.keyboard('{Home}{Delete}');
    expect(onClose).toHaveBeenCalledWith(['a']);
  });
});
