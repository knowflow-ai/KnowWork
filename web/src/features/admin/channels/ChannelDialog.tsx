/**
 * 新建或修改渠道：企业微信、飞书、钉钉机器人。平台建好后不能改（凭据不通用）；Secret 只能写不能读，修改时留空表示不改。
 * 回答方式从接口取：内置两种查知识库，扩展登记的查别的资源（例如数据源），可查的资料跟着回答方式换（ResourceField）。
 * 「提问的人」：不认人时私聊、群聊都只查这里选的资料，能找到机器人的人都能查到，所以页面写明只放大家都可以看的；
 * 认出是谁时私聊按提问人本人的权限查（用选的组织来源认人），选的资料只管群聊、可以不选。没有可选的组织来源时置灰。
 */
import { useState } from 'react';
import { Input } from '@openai/apps-sdk-ui/components/Input';
import { RadioGroup } from '@openai/apps-sdk-ui/components/RadioGroup';
import { Select } from '@openai/apps-sdk-ui/components/Select';
import { Switch } from '@openai/apps-sdk-ui/components/Switch';
import { useQuery } from '@tanstack/react-query';
import {
  BUILT_IN_CHANNEL_MODES,
  createChannel,
  KNOWLEDGE_RESOURCE,
  listChannelAnswerModes,
  platformOf,
  PLATFORMS,
  updateChannel,
  listIdentitySources,
  type AnswerMode,
  type Channel,
  type ChannelAnswerMode,
  type ChannelAudience,
  type ChannelInput,
  type ChannelPlatform,
} from '../../../api/channels';
import { Dialog, DialogActions } from '../../../ui/Dialog';
import { Field } from '../../../ui/Field';
import { toast } from '../../../ui/toastStore';
import { KEYS } from '../queries';
import { useAction } from '../useAction';
import { ResourceField } from './ResourceField';

const ASKS_IN_CHAT = '它追问时直接在 IM 里回答';

/** 回答方式下面那句说明：内置两种写机器人里的特点，扩展的用它自己的说明 */
function modeHelp(mode: ChannelAnswerMode | undefined, member: boolean): string {
  if (!mode) return '原来选的回答方式现在用不了，请重新选';
  if (mode.id === 'agent')
    return member
      ? `能查资料、多步完成任务；${ASKS_IN_CHAT}，需要人工确认时私聊里给网页链接接着确认，群里会停下说明`
      : `能查资料、多步完成任务；${ASKS_IN_CHAT}，需要人工确认的操作机器人做不了，会停下说明`;
  if (mode.id === 'quick') return '只查知识库作答，回答更快';
  return `${mode.description}；${ASKS_IN_CHAT}，结果附前几行和口径，完整的在网页里看`;
}

function initial(channel?: Channel): ChannelInput {
  return {
    name: channel?.name ?? '',
    platform: channel?.platform ?? 'wecom',
    enabled: channel?.enabled ?? true,
    botId: channel?.botId ?? '',
    botName: channel?.botName ?? '',
    secret: '',
    settings: channel?.settings ?? { answerMode: 'agent', bases: [], resources: [], identitySource: null },
    audience: channel?.audience ?? 'public',
  };
}

const AUDIENCES: readonly { readonly value: ChannelAudience; readonly label: string }[] = [
  { value: 'public', label: '不认人' },
  { value: 'member', label: '认出是谁' },
];

/** 企业微信最常见的填错：拿了自建应用的 AgentId（1000002 这种纯数字），智能机器人的 Bot ID 不是纯数字。 */
const looksLikeAgentId = (input: ChannelInput) =>
  input.platform === 'wecom' && /^\d+$/.test(input.botId.trim());

function problem(
  input: ChannelInput,
  editing: boolean,
  mode: ChannelAnswerMode | undefined,
): string | undefined {
  const platform = platformOf(input.platform);
  if (!input.name.trim()) return '请填写名称';
  if (!input.botId.trim()) return `请填写 ${platform.idLabel}`;
  if (looksLikeAgentId(input))
    return '这像是自建应用的 AgentId：请填「智能机器人」API 模式（使用长连接）页面上的 Bot ID';
  if (!editing && !input.secret.trim()) return `请填写 ${platform.secretLabel}`;
  if (!mode) return '请选回答方式';
  if (input.audience === 'member') return input.settings.identitySource ? undefined : '请选认人用的组织来源';
  const chosen = mode.resourceType === KNOWLEDGE_RESOURCE ? input.settings.bases : input.settings.resources;
  return chosen.length === 0 ? `请至少选一个${mode.resourceLabel}` : undefined;
}

export function ChannelDialog({
  channel,
  onClose,
}: {
  readonly channel?: Channel;
  readonly onClose: () => void;
}) {
  const editing = channel !== undefined;
  const [input, setInput] = useState<ChannelInput>(() => initial(channel));
  const modes = useQuery({ queryKey: [...KEYS.channels, 'answer-modes'], queryFn: listChannelAnswerModes });
  const sources = useQuery({
    queryKey: [...KEYS.channels, 'identity-sources', input.platform],
    queryFn: () => listIdentitySources(input.platform),
  });
  const sourceList = sources.data ?? [];
  // 没有可选的组织来源时不能切到认人；已经是认人的照样显示，好改回去
  const canRecognize = sourceList.length > 0 || input.audience === 'member';
  const member = input.audience === 'member';
  const settings = input.settings;
  const modeList = modes.data ?? BUILT_IN_CHANNEL_MODES;
  // 取到之前先按内置的显示；原来选的回答方式不在里面（扩展停了）时要重新选
  const mode = modeList.find((item) => item.id === settings.answerMode);
  const patch = (next: Partial<ChannelInput>) => setInput((current) => ({ ...current, ...next }));
  const patchSettings = (next: Partial<ChannelInput['settings']>) =>
    setInput((current) => ({ ...current, settings: { ...current.settings, ...next } }));
  const action = useAction(
    (value: ChannelInput) => (channel ? updateChannel(channel.id, value) : createChannel(value)),
    {
      invalidate: [KEYS.channels],
      success: editing ? '已保存，机器人会按新配置重新连接' : '已添加，机器人正在连接',
      failure: '保存没有成功，请检查后重试',
    },
  );
  const submit = async () => {
    const found = problem(input, editing, mode);
    if (found) {
      toast.warning(found);
      return;
    }
    if (await action.run(input)) onClose();
  };
  const platform = platformOf(input.platform);
  const secretHelp = channel?.secretSet
    ? `已配置（末四位 ${channel.secretMask || '****'}），留空表示不修改`
    : '保存后不再显示';
  return (
    <Dialog
      title={editing ? `修改渠道 · ${platform.label}` : '添加机器人'}
      size="md"
      onClose={onClose}
      onSubmit={() => void submit()}
      footer={<DialogActions okText="保存" loading={action.pending} onCancel={onClose} />}
    >
      {editing ? null : (
        <Field label="平台" help={platform.guide}>
          <RadioGroup<ChannelPlatform>
            aria-label="平台"
            direction="row"
            value={input.platform}
            onChange={(next) => patch({ platform: next })}
          >
            {PLATFORMS.map((item) => (
              <RadioGroup.Item key={item.value} value={item.value}>
                {item.label}
              </RadioGroup.Item>
            ))}
          </RadioGroup>
        </Field>
      )}
      <Field label="名称" help="管理页里认它用，例如：行政助手">
        {(id) => (
          <Input
            id={id}
            value={input.name}
            maxLength={100}
            onChange={(event) => patch({ name: event.target.value })}
          />
        )}
      </Field>
      <div className="kw-field-row kw-field-row-narrow">
        <Field
          label={platform.idLabel}
          error={
            looksLikeAgentId(input) ? '这像是自建应用的 AgentId，智能机器人的 Bot ID 不是纯数字' : undefined
          }
        >
          {(id) => (
            <Input
              id={id}
              value={input.botId}
              maxLength={128}
              onChange={(event) => patch({ botId: event.target.value })}
            />
          )}
        </Field>
        {input.platform === 'wecom' ? (
          <Field label="机器人名称" help="群里 @ 它时的名字，用来去掉提问开头的 @">
            {(id) => (
              <Input
                id={id}
                value={input.botName}
                maxLength={100}
                onChange={(event) => patch({ botName: event.target.value })}
              />
            )}
          </Field>
        ) : null}
      </div>
      <Field label={platform.secretLabel} help={secretHelp}>
        {(id) => (
          <Input
            id={id}
            type="password"
            autoComplete="new-password"
            placeholder={editing ? '不修改请留空' : ''}
            value={input.secret}
            onChange={(event) => patch({ secret: event.target.value })}
          />
        )}
      </Field>
      <Field label="回答方式" help={modes.isPending && !mode ? undefined : modeHelp(mode, member)}>
        <RadioGroup<AnswerMode>
          aria-label="回答方式"
          direction="row"
          value={settings.answerMode}
          onChange={(answerMode) => patchSettings({ answerMode })}
        >
          {modeList.map((item) => (
            <RadioGroup.Item key={item.id} value={item.id}>
              {item.label}
            </RadioGroup.Item>
          ))}
        </RadioGroup>
      </Field>
      <Field
        label="提问的人"
        help={
          !canRecognize
            ? '要认出是谁，先在「组织架构」里配置同平台的组织来源'
            : member
              ? '私聊按提问人自己的知识库、数据权限回答，会话记在本人名下；群聊回答全群可见，仍按下面的群聊范围查'
              : '所有人都用机器人自己的身份查，能查的就是下面选的知识库'
        }
      >
        <RadioGroup<ChannelAudience>
          aria-label="提问的人"
          direction="row"
          value={input.audience}
          disabled={!canRecognize}
          onChange={(audience) => patch({ audience })}
        >
          {AUDIENCES.map((item) => (
            <RadioGroup.Item key={item.value} value={item.value}>
              {item.label}
            </RadioGroup.Item>
          ))}
        </RadioGroup>
      </Field>
      {member ? (
        <Field
          label="按哪个组织来源认人"
          help={
            sourceList.find((source) => source.id === settings.identitySource)?.login === false
              ? '这个来源没开「用它登录」：没同步到的人认不出来，只能请管理员同步'
              : '没认出的人私聊时会收到一个登录链接，在 IM 里点开登录一次就认出来了'
          }
        >
          <Select
            block
            value={settings.identitySource ?? ''}
            placeholder="选择组织来源"
            options={sourceList.map((source) => ({ value: source.id, label: source.name }))}
            onChange={(chosen) => patchSettings({ identitySource: chosen.value || null })}
          />
        </Field>
      ) : null}
      {mode ? (
        <ResourceField mode={mode} member={member} settings={settings} onChange={patchSettings} />
      ) : null}
      {editing ? (
        <Switch label="启用" checked={input.enabled} onCheckedChange={(enabled) => patch({ enabled })} />
      ) : null}
    </Dialog>
  );
}
