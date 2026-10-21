/** 工具条里的搜索框：组件库的 Input 加搜索图标，有内容时右侧一个清除按钮。宽度固定，不和按钮拼接（界面规范 1.1）。 */
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { Search, X } from '@openai/apps-sdk-ui/components/Icon';
import { Input, type InputProps } from '@openai/apps-sdk-ui/components/Input';
import { cx } from './cx';

interface Props extends Omit<InputProps, 'value' | 'onChange'> {
  readonly value: string;
  readonly onChange: (value: string) => void;
  /** 回车时触发（例如按动作筛选审计日志）。 */
  readonly onSearch?: (value: string) => void;
}

export function SearchInput({ value, onChange, onSearch, className, ...rest }: Props) {
  return (
    <Input
      {...rest}
      className={cx('kw-search', className)}
      value={value}
      onChange={(event) => onChange(event.target.value)}
      onKeyDown={(event) => {
        if (event.key === 'Enter' && onSearch) {
          event.preventDefault();
          onSearch(value);
        }
      }}
      startAdornment={<Search className="fill-secondary" width={16} height={16} />}
      endAdornment={
        value ? (
          <Button
            color="secondary"
            variant="ghost"
            size="3xs"
            uniform
            aria-label="清除"
            onClick={() => {
              onChange('');
              onSearch?.('');
            }}
          >
            <X />
          </Button>
        ) : undefined
      }
    />
  );
}
