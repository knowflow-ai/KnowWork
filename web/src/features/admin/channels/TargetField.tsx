/**
 * 网页嵌入「按谁回答」：自己选回答方式与资料，或者挂一个扩展登记的对象（例如专家，server 的 channels/targets.py）。
 * 挂了就照它答：回答方式、说明、能查的资料都跟着它，它改了自动跟着变，渠道上不再选这些。
 * 从对象自己的页面（例如专家的「嵌入网页」）打开时对象定死了（locked），只写一句说明。
 */
import { Select } from '@openai/apps-sdk-ui/components/Select';
import type { ChannelTarget } from '../../../api/channels';
import { Field } from '../../../ui/Field';

const OWN = '';

function followHelp(target: ChannelTarget, member: boolean): string {
  const who = `${target.label}「${target.name}」`;
  return member
    ? `按${who}的回答方式、说明和能查的资料回答，再按来的人自己的权限收窄；${target.label}改了自动跟着变`
    : `按${who}的回答方式、说明和能查的资料回答，访客都用这个入口自己的身份查它选的资料；${target.label}改了自动跟着变`;
}

export function TargetField({
  value,
  targets,
  locked,
  loading = false,
  member,
  onChange,
}: {
  readonly value: string | null;
  readonly targets: readonly ChannelTarget[];
  /** 从对象自己的页面打开：对象定死了 */
  readonly locked?: ChannelTarget;
  /** 能挂的对象还在取：先别说「删了」 */
  readonly loading?: boolean;
  readonly member: boolean;
  readonly onChange: (target: ChannelTarget | null) => void;
}) {
  if (locked) {
    return (
      <Field label="按谁回答" help={followHelp(locked, member)}>
        <span>
          {locked.label}：{locked.name}
        </span>
      </Field>
    );
  }
  const chosen = targets.find((target) => target.ref === value);
  const kinds = [...new Set(targets.map((target) => target.label))].join('、');
  const help = chosen
    ? followHelp(chosen, member)
    : loading
      ? undefined
      : value
        ? '原来挂的已经删了，请重新选'
        : `自己选回答方式和能查的资料；也可以直接挂一个配好的${kinds}`;
  return (
    <Field label="按谁回答" help={help}>
      <Select
        block
        value={value ?? OWN}
        options={[
          { value: OWN, label: '自己选回答方式和资料' },
          ...targets.map((target) => ({ value: target.ref, label: `${target.label}：${target.name}` })),
        ]}
        onChange={(picked) => onChange(targets.find((target) => target.ref === picked.value) ?? null)}
      />
    </Field>
  );
}
