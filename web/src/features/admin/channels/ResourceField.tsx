/**
 * 渠道里机器人可查的资料：回答方式查知识库时选知识库；查别的（例如数据源）时按授权页登记的那一类选
 * （插槽 grants.resources，列表由扩展给），保存时同步成机器人账号的授权。
 * 不认人时私聊、群聊都只查这些，能找到机器人的人都能查到；认人时只管群聊，可以不选。
 * 网页嵌入：公开访问时访客都只查这些；认证访问按每个人自己的权限，选了只是再收窄，可以不选。
 */
import { Select } from '@openai/apps-sdk-ui/components/Select';
import { useQuery } from '@tanstack/react-query';
import { KNOWLEDGE_RESOURCE, type ChannelAnswerMode, type ChannelSettings } from '../../../api/channels';
import { listKnowledgeBases, type SourceBases } from '../../../api/knowledge';
import { slotItems } from '../../../slots/registry';
import { Field } from '../../../ui/Field';
import { KEYS } from '../queries';

function baseOptions(sources: readonly SourceBases[]) {
  return sources
    .filter((source) => source.enabled && source.bases.length > 0)
    .map((source) => ({
      label: source.sourceName,
      options: source.bases.map((base) => ({ value: base.id, label: base.name })),
    }));
}

function help(member: boolean, label: string, web: boolean): string {
  if (web)
    return member
      ? `按每个人自己能查的${label}回答；选了就只在这几个里面查，不选就不限`
      : `访客都只查这些${label}。能打开这个入口的人都能查到，只放大家都可以看的`;
  return member
    ? `群里的回答全群可见，只放群里大家都可以看的${label}；不选就只开私聊`
    : `私聊和群里都只查这些${label}。能找到这个机器人的人都能查到，只放大家都可以看的；谁能找到机器人在 IM 平台的可见范围里设`;
}

function titleOf(member: boolean, label: string, web: boolean): string {
  if (web) return member ? `限定在这些${label}里` : `可查的${label}`;
  return `${member ? '群聊可查的' : '可查的'}${label}`;
}

export function ResourceField({
  mode,
  member,
  web = false,
  settings,
  onChange,
}: {
  readonly mode: ChannelAnswerMode;
  readonly member: boolean;
  /** 网页嵌入：说明换成网页的说法 */
  readonly web?: boolean;
  readonly settings: ChannelSettings;
  readonly onChange: (next: Partial<ChannelSettings>) => void;
}) {
  const knowledge = mode.resourceType === KNOWLEDGE_RESOURCE;
  const kind = knowledge
    ? undefined
    : slotItems('grants.resources').find((item) => item.type === mode.resourceType);
  const bases = useQuery({ queryKey: KEYS.knowledgeBases, queryFn: listKnowledgeBases, enabled: knowledge });
  const items = useQuery({
    queryKey: [...KEYS.channels, 'resources', mode.resourceType],
    queryFn: () => kind?.load() ?? Promise.resolve([]),
    enabled: kind !== undefined,
  });
  const label = mode.resourceLabel;
  const title = titleOf(member, label, web);
  if (knowledge) {
    return (
      <Field label={title} help={help(member, label, web)}>
        <Select
          block
          multiple
          value={[...settings.bases]}
          placeholder="选择知识库"
          searchPlaceholder="搜索知识库"
          searchEmptyMessage="没有匹配的知识库"
          options={baseOptions(bases.data ?? [])}
          onChange={(chosen) => onChange({ bases: chosen.map((option) => option.value) })}
        />
      </Field>
    );
  }
  return (
    <Field
      label={title}
      help={kind ? help(member, label, web) : `这里列不出可选的${label}，请换一种回答方式`}
    >
      <Select
        block
        multiple
        value={[...settings.resources]}
        placeholder={`选择${label}`}
        searchPlaceholder={`搜索${label}`}
        searchEmptyMessage={`没有匹配的${label}`}
        options={(items.data ?? []).map((item) => ({ value: item.id, label: item.name }))}
        onChange={(chosen) => onChange({ resources: chosen.map((option) => option.value) })}
      />
    </Field>
  );
}
