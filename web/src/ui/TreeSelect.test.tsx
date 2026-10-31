/** 树形选择框：外观同下拉框，点开是带搜索的树；分组节点只展开收起不能选；搜索时留下匹配的和它们的上级。 */
import { useState } from 'react';
import { describe, expect, it } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '../test/user';
import { renderWithApp } from '../test/render';
import { filterTree, labelOf, pathOf, TreeSelect, type TreeSelectNode } from './TreeSelect';

const NODES: readonly TreeSelectNode[] = [
  {
    key: 'source:builtin',
    label: '扩展知识库',
    selectable: false,
    children: [
      { key: 'knowledge_base:builtin.*', label: '全部知识库', children: [] },
      {
        key: 'knowledge_folder:f1',
        label: '产品资料',
        children: [{ key: 'knowledge_base:builtin.kb1', label: '电风扇说明书', children: [] }],
      },
    ],
  },
  {
    key: 'source:kf',
    label: 'KnowFlow',
    selectable: false,
    children: [{ key: 'knowledge_base:kf.d1', label: '制度', children: [] }],
  },
];

function Harness() {
  const [value, setValue] = useState('');
  return (
    <TreeSelect
      aria-label="知识库"
      nodes={NODES}
      value={value}
      onChange={setValue}
      placeholder="选择知识库或目录"
      searchPlaceholder="搜索"
      emptyMessage="没有匹配的选项"
    />
  );
}

describe('TreeSelect', () => {
  it('点开选一个节点，按钮上显示它的完整路径；分组节点点了只展开收起', async () => {
    renderWithApp(<Harness />);
    const trigger = screen.getByRole('button', { name: /选择知识库或目录/ });
    await userEvent.click(trigger);
    const tree = await screen.findByRole('tree', { name: '知识库' });
    await userEvent.click(within(tree).getByRole('button', { name: '扩展知识库' }));
    expect(within(tree).queryByRole('button', { name: '产品资料' })).toBeNull();
    await userEvent.click(within(tree).getByRole('button', { name: '扩展知识库' }));
    await userEvent.click(within(tree).getByRole('button', { name: '电风扇说明书' }));
    expect(screen.queryByRole('tree')).toBeNull();
    expect(
      screen.getByRole('button', { name: /扩展知识库 \/ 产品资料 \/ 电风扇说明书/ }),
    ).toBeInTheDocument();
  });

  it('搜索时只留匹配的节点和它们的上级，匹配目录时带上它下面的', async () => {
    renderWithApp(<Harness />);
    await userEvent.click(screen.getByRole('button', { name: /选择知识库或目录/ }));
    await userEvent.type(screen.getByRole('textbox', { name: '搜索' }), '制度');
    const tree = screen.getByRole('tree', { name: '知识库' });
    expect(within(tree).getByRole('button', { name: '制度' })).toBeInTheDocument();
    expect(within(tree).queryByRole('button', { name: '产品资料' })).toBeNull();
    await userEvent.clear(screen.getByRole('textbox', { name: '搜索' }));
    await userEvent.type(screen.getByRole('textbox', { name: '搜索' }), '查不到');
    expect(screen.getByText('没有匹配的选项')).toBeInTheDocument();
  });
});

function MultiHarness({ seen }: { readonly seen: string[][] }) {
  const [value, setValue] = useState<string[]>([]);
  return (
    <TreeSelect
      multiple
      aria-label="知识库"
      nodes={NODES}
      value={value}
      onChange={(next) => {
        seen.push(next);
        setValue(next);
      }}
      placeholder="选择知识库或目录"
      searchPlaceholder="搜索"
      emptyMessage="没有匹配的选项"
    />
  );
}

describe('TreeSelect 多选', () => {
  it('点一下选上、再点取消，浮层不收；框里列出选中项的名字', async () => {
    const seen: string[][] = [];
    renderWithApp(<MultiHarness seen={seen} />);
    await userEvent.click(screen.getByRole('button', { name: /选择知识库或目录/ }));
    const tree = await screen.findByRole('tree', { name: '知识库' });
    expect(tree).toHaveAttribute('aria-multiselectable', 'true');
    await userEvent.click(within(tree).getByRole('button', { name: '电风扇说明书' }));
    await userEvent.click(within(tree).getByRole('button', { name: '制度' }));
    expect(seen.at(-1)).toEqual(['knowledge_base:builtin.kb1', 'knowledge_base:kf.d1']);
    expect(screen.getByRole('tree', { name: '知识库' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /电风扇说明书、制度/ })).toBeInTheDocument();
    await userEvent.click(within(tree).getByRole('button', { name: '电风扇说明书' }));
    expect(seen.at(-1)).toEqual(['knowledge_base:kf.d1']);
  });
});

describe('树的纯函数', () => {
  it('过滤与路径', () => {
    const kept = filterTree(NODES, '产品');
    expect(kept.map((n) => n.key)).toEqual(['source:builtin']);
    expect(kept[0]?.children.map((n) => n.key)).toEqual(['knowledge_folder:f1']);
    expect(kept[0]?.children[0]?.children.map((n) => n.key)).toEqual(['knowledge_base:builtin.kb1']);
    expect(filterTree(NODES, ' ')).toBe(NODES);
    expect(pathOf(NODES, 'knowledge_base:builtin.kb1')).toBe('扩展知识库 / 产品资料 / 电风扇说明书');
    expect(pathOf(NODES, 'nope')).toBeUndefined();
    expect(labelOf(NODES, 'knowledge_base:builtin.kb1')).toBe('电风扇说明书');
    expect(labelOf(NODES, 'nope')).toBeUndefined();
  });
});
