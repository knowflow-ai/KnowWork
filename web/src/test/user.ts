/**
 * 测试用的 userEvent：不加按键延迟，并关掉 pointer-events 检查。
 * 那项检查每次点击都对所有祖先节点调 getComputedStyle，在 jsdom 里非常慢。
 */
import base, { PointerEventsCheckLevel } from '@testing-library/user-event';
import { screen, within } from '@testing-library/react';

export const userEvent = base.setup({ delay: null, pointerEventsCheck: PointerEventsCheckLevel.Never });
export default userEvent;

/** 在以 group 命名的表单项（Field）里打开组件库的选择框，选中一项。 */
export async function pickOption(scope: HTMLElement, group: string, option: string | RegExp) {
  await userEvent.click(within(within(scope).getByRole('group', { name: group })).getByRole('button'));
  await userEvent.click(await screen.findByRole('option', { name: option }));
}

/** 树形选择框（ui/TreeSelect）：点开表单项里的选择框，在树里点名字。树默认全部展开。 */
export async function pickTreeNode(scope: HTMLElement, group: string, node: string | RegExp) {
  await userEvent.click(within(within(scope).getByRole('group', { name: group })).getByRole('button'));
  const tree = await screen.findByRole('tree', { name: group });
  await userEvent.click(within(tree).getByRole('button', { name: node }));
}
