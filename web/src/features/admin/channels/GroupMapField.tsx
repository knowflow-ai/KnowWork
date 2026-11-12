/**
 * 网页嵌入认证访问的组对照：接入方系统里的组（角色）→ KnowWork 的用户组。外部用户每次进来按接入方给的组，同步对照里
 * 这几个用户组的成员身份（服务端 channels/web_identity.py），权限按用户组配，人员变动不用改。一行一个，没填全的行不保存。
 * 权限只加不减：要单独收窄某个人，就别让他进大组。
 */
import { useState } from 'react';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { Plus, X } from '@openai/apps-sdk-ui/components/Icon';
import { Input } from '@openai/apps-sdk-ui/components/Input';
import { Select } from '@openai/apps-sdk-ui/components/Select';
import { useQuery } from '@tanstack/react-query';
import { listGroups } from '../../../api/workspace';
import { Field } from '../../../ui/Field';
import { KEYS } from '../queries';

interface Row {
  /** 只在本页用的行号：删一行时别的行不串 */
  readonly id: number;
  readonly name: string;
  readonly group: string;
}

let nextRow = 0;
const row = (name = '', group = ''): Row => ({ id: (nextRow += 1), name, group });

const MAX_ROWS = 50;

const mapOf = (rows: readonly Row[]): Record<string, string> =>
  Object.fromEntries(
    rows.filter((item) => item.name.trim() && item.group).map((item) => [item.name.trim(), item.group]),
  );

export function GroupMapField({
  value,
  onChange,
}: {
  readonly value: Readonly<Record<string, string>>;
  readonly onChange: (next: Record<string, string>) => void;
}) {
  const groups = useQuery({ queryKey: [...KEYS.channels, 'groups'], queryFn: listGroups });
  // 原样留在本地：正在填的半行不能一改就被丢掉
  const [rows, setRows] = useState<readonly Row[]>(() =>
    Object.entries(value).map(([name, group]) => row(name, group)),
  );
  const update = (next: readonly Row[]) => {
    setRows(next);
    onChange(mapOf(next));
  };
  const options = (groups.data ?? []).map((group) => ({ value: group.id, label: group.name }));
  return (
    <Field
      label="组对照"
      help="接入方系统里的组（角色）对到这里的用户组：外部用户每次进来按它进出这几个组，按用户组授权即可。没有用户组先到「成员与权限 → 用户组」建。"
    >
      <div className="kw-group-map">
        {rows.map((item) => (
          <div className="kw-group-map-row" key={item.id}>
            <Input
              aria-label="接入方的组"
              placeholder="接入方的组名，例如：华东区域经理"
              value={item.name}
              maxLength={100}
              onChange={(event) =>
                update(rows.map((one) => (one.id === item.id ? { ...one, name: event.target.value } : one)))
              }
            />
            <Select
              block
              value={item.group}
              placeholder="选择用户组"
              options={options}
              onChange={(chosen) =>
                update(rows.map((one) => (one.id === item.id ? { ...one, group: chosen.value } : one)))
              }
            />
            <Button
              color="secondary"
              variant="ghost"
              uniform
              aria-label="删掉这一行"
              onClick={() => update(rows.filter((one) => one.id !== item.id))}
            >
              <X />
            </Button>
          </div>
        ))}
        {rows.length < MAX_ROWS ? (
          <div>
            <Button color="secondary" variant="outline" size="sm" onClick={() => update([...rows, row()])}>
              <Plus />
              添加一行
            </Button>
          </div>
        ) : null}
      </div>
    </Field>
  );
}
