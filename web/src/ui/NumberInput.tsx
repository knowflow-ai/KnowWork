/** 数字输入：组件库的 Input 加上取整与上下限，空值返回 null。 */
import { Input, type InputProps } from '@openai/apps-sdk-ui/components/Input';

interface Props extends Omit<InputProps, 'value' | 'onChange' | 'type' | 'min' | 'max'> {
  readonly value: number | null | undefined;
  readonly onChange: (value: number | null) => void;
  readonly min?: number;
  readonly max?: number;
  /** 允许小数（价格这类）；默认只要整数。 */
  readonly decimal?: boolean;
}

export function NumberInput({ value, onChange, min, max, decimal, ...rest }: Props) {
  return (
    <Input
      {...rest}
      type="number"
      inputMode={decimal ? 'decimal' : 'numeric'}
      min={min}
      max={max}
      step={decimal ? 'any' : 1}
      value={value ?? ''}
      onChange={(event) => {
        const text = event.target.value;
        if (text === '') return onChange(null);
        const parsed = decimal ? Number(text) : Math.trunc(Number(text));
        if (Number.isNaN(parsed)) return;
        onChange(parsed);
      }}
    />
  );
}
