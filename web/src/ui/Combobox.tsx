/**
 * 边输边搜的选择框：结果由调用方按输入去服务端查（组件库的 Select 只能在已有选项里筛）。
 * 输入框是 combobox，结果在下方浮层里（Radix Popover，放进 portal，不会被对话框的滚动区裁掉）；
 * 上下键挑选、回车选中、Esc 收起。样式照组件库的 Select 菜单。
 */
import { useId, useState, type KeyboardEvent, type ReactNode } from 'react';
import { Popover } from 'radix-ui';
import { Input } from '@openai/apps-sdk-ui/components/Input';
import { LoadingIndicator } from '@openai/apps-sdk-ui/components/Indicator';
import { Search } from '@openai/apps-sdk-ui/components/Icon';
import { cx } from './cx';

export interface ComboboxOption {
  readonly value: string;
  readonly label: string;
}

interface Props {
  readonly query: string;
  readonly onQueryChange: (query: string) => void;
  readonly options: readonly ComboboxOption[];
  readonly onSelect: (option: ComboboxOption) => void;
  readonly loading?: boolean;
  /** 输入了但没有结果时的说明。还没输入时不弹出，提示写在 placeholder 里。 */
  readonly empty?: ReactNode;
  readonly placeholder?: string;
  readonly id?: string;
  readonly 'aria-label'?: string;
  readonly invalid?: boolean;
}

export function Combobox({
  query,
  onQueryChange,
  options,
  onSelect,
  loading,
  empty,
  placeholder,
  id,
  invalid,
  ...rest
}: Props) {
  const listId = useId();
  const [wanted, setOpen] = useState(false);
  // 还没输入时不弹：对话框打开时焦点会自动落到这里，空着就弹出一块提示会盖住下面的表单项
  const open = wanted && query.trim().length > 0;
  const [active, setActive] = useState(0);
  const index = Math.min(active, Math.max(0, options.length - 1));
  const pick = (option: ComboboxOption) => {
    onSelect(option);
    setOpen(false);
  };
  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      setOpen(true);
      const step = event.key === 'ArrowDown' ? 1 : -1;
      setActive((index + step + options.length) % Math.max(1, options.length));
    } else if (event.key === 'Enter' && open && options[index]) {
      event.preventDefault();
      pick(options[index]);
    } else if (event.key === 'Escape' && open) {
      // 只收起结果，不让外层对话框跟着关
      event.stopPropagation();
      setOpen(false);
    }
  };
  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Anchor asChild>
        <div>
          <Input
            id={id}
            role="combobox"
            aria-label={rest['aria-label']}
            aria-expanded={open}
            aria-controls={listId}
            aria-autocomplete="list"
            autoComplete="off"
            invalid={invalid}
            placeholder={placeholder}
            startAdornment={<Search className="fill-secondary" width={16} height={16} />}
            endAdornment={loading ? <LoadingIndicator size={14} /> : undefined}
            value={query}
            onChange={(event) => {
              onQueryChange(event.target.value);
              setActive(0);
              setOpen(true);
            }}
            onFocus={() => setOpen(true)}
            onKeyDown={onKeyDown}
          />
        </div>
      </Popover.Anchor>
      <Popover.Portal>
        <Popover.Content
          // Radix 默认把浮层标成 dialog，这里只是一列候选，交给里面的 listbox 表达
          role="presentation"
          className="kw-combobox-menu"
          align="start"
          sideOffset={4}
          style={{ width: 'var(--radix-popover-trigger-width)' }}
          onOpenAutoFocus={(event) => event.preventDefault()}
          // 点输入框本身不算点到外面
          onInteractOutside={(event) => {
            const target = event.target as Node | null;
            if (target && document.getElementById(id ?? '')?.contains(target)) event.preventDefault();
          }}
        >
          <div id={listId} role="listbox" aria-label={rest['aria-label']}>
            {options.length === 0 ? (
              <div className="kw-combobox-empty">{loading ? '正在搜索…' : empty}</div>
            ) : (
              options.map((option, i) => (
                <div
                  key={option.value}
                  role="option"
                  aria-selected={i === index}
                  className={cx('kw-combobox-option', i === index && 'is-active')}
                  // 不抢输入框的焦点
                  onMouseDown={(event) => event.preventDefault()}
                  onMouseEnter={() => setActive(i)}
                  onClick={() => pick(option)}
                >
                  {option.label}
                </div>
              ))
            )}
          </div>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
