import { describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import userEvent from '../../test/user';
import { renderWithApp } from '../../test/render';
import { ZoomControls } from './ZoomControls';

function setup(zoom: number, fitWidth = false) {
  const onZoom = vi.fn();
  const onFitWidth = vi.fn();
  const onActiveChange = vi.fn();
  renderWithApp(
    <ZoomControls
      zoom={zoom}
      fitWidth={fitWidth}
      visible
      onZoom={onZoom}
      onFitWidth={onFitWidth}
      onActiveChange={onActiveChange}
    />,
  );
  return { onZoom, onFitWidth, onActiveChange };
}

describe('ZoomControls', () => {
  it('放大缩小按 25% 一档，显示当前比例', async () => {
    const { onZoom } = setup(0.86);
    expect(screen.getByRole('button', { name: '缩放比例' })).toHaveTextContent('86%');
    await userEvent.click(screen.getByRole('button', { name: '放大' }));
    await userEvent.click(screen.getByRole('button', { name: '缩小' }));
    expect(onZoom.mock.calls).toEqual([[1], [0.75]]);
  });

  it('菜单里选适应宽度或固定档；到头的方向按钮不可点', async () => {
    const { onZoom, onFitWidth, onActiveChange } = setup(4);
    expect(screen.getByRole('button', { name: '放大' })).toBeDisabled();
    await userEvent.click(screen.getByRole('button', { name: '缩放比例' }));
    expect(onActiveChange).toHaveBeenLastCalledWith(true);
    fireEvent.click(await screen.findByRole('menuitemradio', { name: '150%' }));
    expect(onZoom).toHaveBeenCalledWith(1.5);
    // 菜单关闭有退场过程，等上一个菜单真正卸掉再打开，否则点到的是正在退场的旧菜单项
    await waitFor(() => expect(screen.queryByRole('menu')).not.toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: '缩放比例' }));
    fireEvent.click(await screen.findByRole('menuitemradio', { name: '适应宽度' }));
    expect(onFitWidth).toHaveBeenCalled();
  });
});
