/** 从本空间成员里选人（部门、用户组只能加空间成员），可按姓名、邮箱搜索。 */
import { Select } from '@openai/apps-sdk-ui/components/Select';
import { memberLabel, type Member } from '../../../api/workspace';

interface Props {
  readonly members: readonly Member[];
  readonly exclude: ReadonlySet<string>;
  readonly value?: string;
  readonly onChange: (userId: string) => void;
}

export function MemberSelect({ members, exclude, value, onChange }: Props) {
  const options = members
    .filter((m) => !exclude.has(m.userId))
    .map((m) => ({ value: m.userId, label: m.email ? `${memberLabel(m)}（${m.email}）` : memberLabel(m) }));
  return (
    <div role="group" aria-label="选择成员" className="kw-member-select">
      <Select
        block
        options={options}
        value={value ?? ''}
        placeholder="选择空间成员"
        searchPlaceholder="按姓名或邮箱搜索"
        searchEmptyMessage="没有可添加的成员"
        onChange={(option) => onChange(option.value)}
      />
    </div>
  );
}
