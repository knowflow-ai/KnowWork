import { describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { Input } from '@openai/apps-sdk-ui/components/Input';
import { Select } from '@openai/apps-sdk-ui/components/Select';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { Dialog, DialogActions } from './Dialog';
import { Field } from './Field';
import { Table, type Column } from './Table';
import { Tabs } from './Tabs';
import { Sheet } from './Sheet';
import { Toaster } from './Toaster';
import { toast } from './toastStore';
import { ConfirmHost, ConfirmPopover } from './confirm';
import { confirm } from './confirmStore';
import { NumberInput } from './NumberInput';
import { StatusDot } from './StatusDot';
import { DescriptionList } from './DescriptionList';
import { Combobox } from './Combobox';

describe('Dialog', () => {
  it('回车提交表单，关闭按钮与取消都触发 onClose', async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    const onClose = vi.fn();
    render(
      <Dialog
        title="重命名"
        onSubmit={onSubmit}
        onClose={onClose}
        footer={<DialogActions okText="保存" onCancel={onClose} />}
      >
        <Field label="名称">{(id) => <Input id={id} defaultValue="a" />}</Field>
      </Dialog>,
    );
    const dialog = screen.getByRole('dialog', { name: '重命名' });
    await user.type(within(dialog).getByLabelText('名称'), 'b{Enter}');
    expect(onSubmit).toHaveBeenCalledTimes(1);
    await user.click(within(dialog).getByRole('button', { name: '保存' }));
    expect(onSubmit).toHaveBeenCalledTimes(2);
    await user.click(within(dialog).getByRole('button', { name: '取消' }));
    await user.click(within(dialog).getByRole('button', { name: '关闭' }));
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it('确定按钮传 onOk 时是普通按钮，可以显示加载中', async () => {
    const user = userEvent.setup();
    const onOk = vi.fn();
    render(
      <Dialog title="删除" onClose={vi.fn()} footer={<DialogActions okText="删除" danger onOk={onOk} />}>
        正文
      </Dialog>,
    );
    await user.click(screen.getByRole('button', { name: '删除' }));
    expect(onOk).toHaveBeenCalled();
  });
});

describe('Sheet', () => {
  it('有标题、右侧操作与关闭', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <Sheet title="报告.pdf" extra={<button type="button">下载</button>} onClose={onClose}>
        内容
      </Sheet>,
    );
    const sheet = screen.getByRole('dialog', { name: '报告.pdf' });
    expect(within(sheet).getByRole('button', { name: '下载' })).toBeInTheDocument();
    await user.click(within(sheet).getByRole('button', { name: '关闭' }));
    expect(onClose).toHaveBeenCalled();
  });
});

describe('Field', () => {
  it('按钮式控件整块按标签命名，错误替换说明', () => {
    render(
      <Field label="角色" help="决定能做什么" error="请选择角色">
        <Select options={[{ value: 'a', label: '管理员' }]} value="" placeholder="选择" onChange={vi.fn()} />
      </Field>,
    );
    const group = screen.getByRole('group', { name: '角色' });
    expect(within(group).getByRole('button', { name: '选择' })).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('请选择角色');
    expect(screen.queryByText('决定能做什么')).not.toBeInTheDocument();
  });
});

interface Row {
  readonly id: string;
  readonly name: string;
}

const COLUMNS: readonly Column<Row>[] = [{ key: 'name', title: '名称', render: (row) => row.name }];

describe('Table', () => {
  const rows = Array.from({ length: 23 }, (_, i) => ({ id: String(i), name: `第${i + 1}行` }));

  it('分页带「共 N 条」，可翻页，行可点', async () => {
    const user = userEvent.setup();
    const onRowClick = vi.fn();
    render(<Table columns={COLUMNS} rows={rows} rowKey={(r) => r.id} onRowClick={onRowClick} />);
    expect(screen.getByText('共 23 条')).toBeInTheDocument();
    expect(screen.getByText('第10行')).toBeInTheDocument();
    expect(screen.queryByText('第11行')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '3' }));
    expect(screen.getByText('第23行')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '上一页' }));
    expect(screen.getByText('第11行')).toBeInTheDocument();
    await user.click(screen.getByText('第12行'));
    expect(onRowClick).toHaveBeenCalledWith(rows[11]);
  });

  it('数据变少时停在最后一页；没有数据显示空状态', () => {
    const { rerender } = render(<Table columns={COLUMNS} rows={rows} rowKey={(r) => r.id} />);
    rerender(<Table columns={COLUMNS} rows={[]} rowKey={(r) => r.id} empty="还没有数据" />);
    expect(screen.getByText('还没有数据')).toBeInTheDocument();
    expect(screen.getByText('共 0 条')).toBeInTheDocument();
  });

  it('页码多时中间用省略号', async () => {
    const user = userEvent.setup();
    const many = Array.from({ length: 200 }, (_, i) => ({ id: String(i), name: `r${i}` }));
    render(<Table columns={COLUMNS} rows={many} rowKey={(r) => r.id} pageSize={10} />);
    await user.click(screen.getByRole('button', { name: '20' }));
    await user.click(screen.getByRole('button', { name: '19' }));
    expect(screen.getAllByText('…')).toHaveLength(1);
    expect(screen.getByRole('button', { name: '19' })).toHaveAttribute('aria-current', 'page');
  });
});

describe('Tabs', () => {
  it('切换标签页', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <Tabs
        onChange={onChange}
        items={[
          { key: 'a', label: '服务商', children: '服务商列表' },
          { key: 'b', label: '模型', children: '模型列表' },
        ]}
      />,
    );
    expect(screen.getByText('服务商列表')).toBeVisible();
    await user.click(screen.getByRole('tab', { name: '模型' }));
    expect(onChange).toHaveBeenCalledWith('b');
    expect(screen.getByText('模型列表')).toBeVisible();
  });
});

describe('toast', () => {
  it('提示 3 秒后消失，出错的用 alert', async () => {
    vi.useFakeTimers();
    try {
      render(<Toaster />);
      act(() => {
        toast.success('已保存');
        toast.error('保存没有成功');
      });
      expect(screen.getByRole('status')).toHaveTextContent('已保存');
      expect(screen.getByRole('alert')).toHaveTextContent('保存没有成功');
      act(() => vi.advanceTimersByTime(3100));
      expect(screen.queryByText('已保存')).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('confirm', () => {
  it('对话框确认返回用户的选择', async () => {
    const user = userEvent.setup();
    render(<ConfirmHost />);
    let answer: Promise<boolean> | undefined;
    act(() => {
      answer = confirm({ title: '删除这个模型？', okText: '删除', danger: true });
    });
    await user.click(await screen.findByRole('button', { name: '删除' }));
    await expect(answer).resolves.toBe(true);
    act(() => {
      answer = confirm({ title: '再删一个？', okText: '删除' });
    });
    await user.click(await screen.findByRole('button', { name: '取消' }));
    await expect(answer).resolves.toBe(false);
  });

  it('行内确认点了确定才执行', async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    render(
      <ConfirmPopover title="移出这个成员？" okText="移出" danger onConfirm={onConfirm}>
        <button type="button">移出</button>
      </ConfirmPopover>,
    );
    await user.click(screen.getByRole('button', { name: '移出' }));
    const pop = await screen.findByRole('alertdialog', { name: '移出这个成员？' });
    await user.click(within(pop).getByRole('button', { name: '移出' }));
    expect(onConfirm).toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument());
  });
});

describe('小组件', () => {
  it('数字输入：空值为 null，整数取整', async () => {
    const user = userEvent.setup();
    const values: (number | null)[] = [];
    function Demo() {
      const [value, setValue] = useState<number | null>(3);
      return (
        <NumberInput
          aria-label="上限"
          value={value}
          onChange={(next) => {
            values.push(next);
            setValue(next);
          }}
        />
      );
    }
    render(<Demo />);
    await user.clear(screen.getByLabelText('上限'));
    await user.type(screen.getByLabelText('上限'), '12');
    expect(values).toEqual([null, 1, 12]);
  });

  it('状态点与详情列表', () => {
    render(
      <>
        <StatusDot tone="success">正常</StatusDot>
        <DescriptionList items={[{ label: '地址', value: 'https://x' }]} />
      </>,
    );
    expect(screen.getByText('正常')).toBeInTheDocument();
    expect(screen.getByText('地址').tagName).toBe('DT');
  });
});

describe('抽屉里的行内确认', () => {
  it('对话框的焦点陷阱不会把气泡关掉', async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    render(
      <Sheet title="成员" onClose={vi.fn()}>
        <ConfirmPopover title="移出？" okText="移出" danger onConfirm={onConfirm}>
          <Button color="danger" variant="ghost">
            移出
          </Button>
        </ConfirmPopover>
      </Sheet>,
    );
    await user.click(screen.getByRole('button', { name: '移出' }));
    const pop = await screen.findByRole('alertdialog', { name: '移出？' });
    await waitFor(() => expect(within(pop).getByRole('button', { name: '取消' })).toHaveFocus());
    await user.click(within(pop).getByRole('button', { name: '移出' }));
    expect(onConfirm).toHaveBeenCalled();
  });
});

describe('只显示一次的内容', () => {
  it('点遮罩不关，关闭按钮照常可用', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <Dialog title="已创建" closeOnOutside={false} onClose={onClose}>
        密钥
      </Dialog>,
    );
    await user.click(document.querySelector('.kw-overlay') as HTMLElement);
    expect(onClose).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: '关闭' }));
    expect(onClose).toHaveBeenCalled();
  });
});

describe('Combobox', () => {
  function Demo({ onSelect }: { readonly onSelect: (value: string) => void }) {
    const [query, setQuery] = useState('');
    const all = [
      { value: 'u2', label: '韩梅梅（han@corp.cn）' },
      { value: 'u3', label: '魏华（wei@corp.cn）' },
    ];
    return (
      <Combobox
        aria-label="选择用户"
        query={query}
        onQueryChange={setQuery}
        options={query ? all.filter((o) => o.label.includes(query)) : []}
        empty="没有找到可添加的用户"
        onSelect={(option) => {
          setQuery(option.label);
          onSelect(option.value);
        }}
      />
    );
  }

  it('获得焦点但还没输入时不弹出，免得盖住下面的表单项', async () => {
    const user = userEvent.setup();
    render(<Demo onSelect={vi.fn()} />);
    await user.click(screen.getByRole('combobox', { name: '选择用户' }));
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
    expect(screen.getByRole('combobox')).toHaveAttribute('aria-expanded', 'false');
  });

  it('输入后列出候选，上下键挑、回车选中；没有结果时说明', async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(<Demo onSelect={onSelect} />);
    const input = screen.getByRole('combobox', { name: '选择用户' });
    await user.type(input, 'corp');
    expect(await screen.findAllByRole('option')).toHaveLength(2);
    await user.keyboard('{ArrowDown}{Enter}');
    expect(onSelect).toHaveBeenCalledWith('u3');
    await user.clear(input);
    await user.type(input, 'zzz');
    expect(await screen.findByText('没有找到可添加的用户')).toBeInTheDocument();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });
});
