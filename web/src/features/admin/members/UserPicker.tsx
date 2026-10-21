/** 按邮箱或姓名搜索账号的选择框（只有空间管理员能搜）。输入停顿后再请求，已是成员的不出现。 */
import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { searchUsers } from '../../../api/identity';
import { Combobox } from '../../../ui/Combobox';

interface Props {
  readonly id?: string;
  readonly onChange: (userId: string | undefined) => void;
  readonly exclude: ReadonlySet<string>;
  readonly invalid?: boolean;
}

const DEBOUNCE_MS = 300;

function useDebounced(value: string, delay: number): string {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}

export function UserPicker({ id, onChange, exclude, invalid }: Props) {
  const [keyword, setKeyword] = useState('');
  const term = useDebounced(keyword.trim(), DEBOUNCE_MS);
  const result = useQuery({
    queryKey: ['admin', 'user-search', term],
    queryFn: () => searchUsers(term),
    enabled: term.length > 0,
    staleTime: 30_000,
  });
  const options = (result.data ?? [])
    .filter((user) => !exclude.has(user.id))
    .map((user) => ({ value: user.id, label: `${user.name}（${user.email}）` }));
  return (
    <Combobox
      id={id}
      aria-label="选择用户"
      placeholder="输入邮箱或姓名搜索"
      invalid={invalid}
      query={keyword}
      onQueryChange={(next) => {
        setKeyword(next);
        // 改了输入就要重新选，免得框里写的和实际选中的不是一个人
        onChange(undefined);
      }}
      options={term ? options : []}
      // 输入后到防抖结束、请求回来之前都算在搜
      loading={result.isFetching || keyword.trim() !== term}
      empty="没有找到可添加的用户"
      onSelect={(option) => {
        setKeyword(option.label);
        onChange(option.value);
      }}
    />
  );
}
