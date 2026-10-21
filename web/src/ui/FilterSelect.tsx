/**
 * 工具条与表格里不带可见标签的选择框：外面包一层以 label 命名的 group，读屏与测试都按它找到。
 * 可清空时空值是 ''，对外换成 undefined（「不按这一项筛选」）。
 */
import { Select } from '@openai/apps-sdk-ui/components/Select';

interface Props<T extends string> {
  readonly label: string;
  readonly options: readonly { readonly value: T; readonly label: string }[];
  readonly value: T | undefined;
  readonly onChange: (value: T | undefined) => void;
  /** 可清空时显示清除按钮；不选时显示 label 作为占位。 */
  readonly clearable?: boolean;
  readonly width?: number;
  readonly size?: 'sm' | 'md';
}

export function FilterSelect<T extends string>({
  label,
  options,
  value,
  onChange,
  clearable,
  width,
  size,
}: Props<T>) {
  return (
    <div role="group" aria-label={label} style={{ width }}>
      <Select
        block
        size={size}
        clearable={clearable}
        placeholder={label}
        // 选项超过 15 个时组件库会自动出搜索框
        searchPlaceholder={`搜索${label}`}
        searchEmptyMessage="没有匹配的选项"
        options={options.map((o) => ({ ...o }))}
        value={value ?? ''}
        onChange={(option) => onChange(option.value ? (option.value as T) : undefined)}
      />
    </div>
  );
}
