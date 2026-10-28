/**
 * 新建或修改渠道：企业微信、飞书、钉钉机器人。平台建好后不能改（凭据不通用）；Secret 只能写不能读，修改时留空表示不改。
 * 机器人不认人：私聊、群聊都只查这里选的知识库，能找到机器人的人都能查到，所以页面写明只放大家都可以看的。
 */
import { useState } from 'react';
import { Input } from '@openai/apps-sdk-ui/components/Input';
import { RadioGroup } from '@openai/apps-sdk-ui/components/RadioGroup';
import { Select } from '@openai/apps-sdk-ui/components/Select';
import { Switch } from '@openai/apps-sdk-ui/components/Switch';
import { useQuery } from '@tanstack/react-query';
import {
  createChannel,
  platformOf,
  PLATFORMS,
  updateChannel,
  type AnswerMode,
  type Channel,
  type ChannelInput,
  type ChannelPlatform,
} from '../../../api/channels';
import { listKnowledgeBases, type SourceBases } from '../../../api/knowledge';
import { Dialog, DialogActions } from '../../../ui/Dialog';
import { Field } from '../../../ui/Field';
import { toast } from '../../../ui/toastStore';
import { KEYS } from '../queries';
import { useAction } from '../useAction';

const ANSWER_MODES: readonly { readonly value: AnswerMode; readonly label: string; readonly help: string }[] =
  [
    {
      value: 'agent',
      label: '智能体',
      help: '能查资料、多步完成任务；需要人工确认的操作机器人做不了，会停下说明',
    },
    { value: 'quick', label: '快速问答', help: '只检索知识库作答，回答更快' },
  ];

function initial(channel?: Channel): ChannelInput {
  return {
    name: channel?.name ?? '',
    platform: channel?.platform ?? 'wecom',
    enabled: channel?.enabled ?? true,
    botId: channel?.botId ?? '',
    botName: channel?.botName ?? '',
    secret: '',
    settings: channel?.settings ?? { answerMode: 'agent', bases: [] },
  };
}

function baseOptions(sources: readonly SourceBases[]) {
  return sources
    .filter((source) => source.enabled && source.bases.length > 0)
    .map((source) => ({
      label: source.sourceName,
      options: source.bases.map((base) => ({ value: base.id, label: base.name })),
    }));
}

/** 企业微信最常见的填错：拿了自建应用的 AgentId（1000002 这种纯数字），智能机器人的 Bot ID 不是纯数字。 */
const looksLikeAgentId = (input: ChannelInput) =>
  input.platform === 'wecom' && /^\d+$/.test(input.botId.trim());

function problem(input: ChannelInput, editing: boolean): string | undefined {
  const platform = platformOf(input.platform);
  if (!input.name.trim()) return '请填写名称';
  if (!input.botId.trim()) return `请填写 ${platform.idLabel}`;
  if (looksLikeAgentId(input))
    return '这像是自建应用的 AgentId：请填「智能机器人」API 模式（使用长连接）页面上的 Bot ID';
  if (!editing && !input.secret.trim()) return `请填写 ${platform.secretLabel}`;
  if (input.settings.bases.length === 0) return '请至少选一个知识库';
  return undefined;
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
  const bases = useQuery({ queryKey: KEYS.knowledgeBases, queryFn: listKnowledgeBases });
  const options = baseOptions(bases.data ?? []);
  const settings = input.settings;
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
    const found = problem(input, editing);
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
      <Field label="回答方式" help={ANSWER_MODES.find((mode) => mode.value === settings.answerMode)?.help}>
        <RadioGroup<AnswerMode>
          aria-label="回答方式"
          direction="row"
          value={settings.answerMode}
          onChange={(answerMode) => patchSettings({ answerMode })}
        >
          {ANSWER_MODES.map((mode) => (
            <RadioGroup.Item key={mode.value} value={mode.value}>
              {mode.label}
            </RadioGroup.Item>
          ))}
        </RadioGroup>
      </Field>
      <Field
        label="可查的知识库"
        help="私聊和群里都只查这些。能找到这个机器人的人都能查到，只放大家都可以看的；谁能找到机器人在 IM 平台的可见范围里设"
      >
        <Select
          block
          multiple
          value={[...settings.bases]}
          placeholder="选择知识库"
          searchPlaceholder="搜索知识库"
          searchEmptyMessage="没有匹配的知识库"
          options={options}
          onChange={(chosen) => patchSettings({ bases: chosen.map((option) => option.value) })}
        />
      </Field>
      {editing ? (
        <Switch label="启用" checked={input.enabled} onCheckedChange={(enabled) => patch({ enabled })} />
      ) : null}
    </Dialog>
  );
}
