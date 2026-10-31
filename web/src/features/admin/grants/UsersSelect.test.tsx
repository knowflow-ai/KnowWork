/** 「给谁用」：树形多选，下级部门挂在上级下面；点一下选上、再点取消，浮层不收；没有内容的组不出现。 */
import { useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '../../../test/user';
import { jsonResponse, renderWithApp, routeFetch } from '../../../test/render';
import { UsersSelect } from './UsersSelect';

const B = '/api/workspaces/default';

afterEach(() => vi.unstubAllGlobals());

function api() {
  return routeFetch({
    [`GET ${B}/members`]: jsonResponse([
      { user_id: 'u1', role: 'owner', name: '李雷', email: 'li@corp.cn' },
      { user_id: 'u2', role: 'editor', name: '韩梅梅', email: 'han@corp.cn' },
    ]),
    [`GET ${B}/departments`]: jsonResponse([
      {
        id: 'd1',
        name: '测试部门',
        parent_id: null,
        members: [],
        children: [{ id: 'd2', name: 'sub', parent_id: 'd1', members: [], children: [] }],
      },
    ]),
    [`GET ${B}/groups`]: jsonResponse([]),
  });
}

function Harness({ seen }: { readonly seen: string[][] }) {
  const [value, setValue] = useState<string[]>([]);
  return (
    <UsersSelect
      value={value}
      onChange={(next) => {
        seen.push(next);
        setValue(next);
      }}
    />
  );
}

describe('给谁用', () => {
  it('部门按层级成树，可以选几个、再点取消', async () => {
    api();
    const seen: string[][] = [];
    renderWithApp(<Harness seen={seen} />);
    await userEvent.click(screen.getByRole('button', { name: /只有管理员/ }));
    const tree = await screen.findByRole('tree', { name: '给谁用' });
    const parent = (await within(tree).findByRole('button', { name: '测试部门' })).closest('li');
    expect(within(parent as HTMLElement).getByRole('button', { name: 'sub' })).toBeInTheDocument();
    expect(within(tree).queryByRole('button', { name: '用户组' })).toBeNull();

    await userEvent.click(within(tree).getByRole('button', { name: 'sub' }));
    await userEvent.click(await within(tree).findByRole('button', { name: '韩梅梅' }));
    expect(seen.at(-1)).toEqual(['dept:d2', 'user:u2']);
    expect(screen.getByRole('tree', { name: '给谁用' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /sub、韩梅梅/ })).toBeInTheDocument();

    await userEvent.click(within(tree).getByRole('button', { name: 'sub' }));
    expect(seen.at(-1)).toEqual(['user:u2']);
    await userEvent.click(within(tree).getByRole('button', { name: '部门' }));
    expect(seen).toHaveLength(3);
  });
});
