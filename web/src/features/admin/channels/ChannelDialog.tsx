/**
 * 新建或修改渠道：企业微信、飞书、钉钉机器人，或网页嵌入。平台建好后不能改（凭据不通用）；Secret 只能写不能读，修改时留空表示不改。
 * 网页嵌入没有机器人与 Secret，现在只能公开访问（访客不用登录、都用渠道账号查），另填允许嵌入的网站、外观、限次（WebFields）。
 * 回答方式从接口取：内置两种查知识库，扩展登记的查别的资源（例如数据源），可查的资料跟着回答方式换（ResourceField）。
 * 「提问的人」：不认人时私聊、群聊都只查这里选的资料，能找到机器人的人都能查到，所以页面写明只放大家都可以看的；
 * 认出是谁时私聊按提问人本人的权限查（用选的组织来源认人），选的资料只管群聊、可以不选。没有可选的组织来源时置灰。
 * 网页嵌入可以挂一个扩展登记的对象（例如专家，TargetField）：挂了就不再选回答方式与资料。从对象自己的页面打开时（preset）
 * 平台定为网页嵌入、对象定死。
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
  DEFAULT_WEB_OPTIONS,
  KNOWLEDGE_RESOURCE,
  listChannelAnswerModes,
  platformOf,
  PLATFORMS,
  updateChannel,
  listIdentitySources,
  listChannelTargets,
  type AnswerMode,
  type Channel,
  type ChannelAnswerMode,
  type ChannelAudience,
  type ChannelInput,
  type ChannelPlatform,
  type ChannelTarget,
} from '../../../api/channels';
import { Dialog, DialogActions } from '../../../ui/Dialog';
import { Field } from '../../../ui/Field';
import { toast } from '../../../ui/toastStore';
import { KEYS } from '../queries';
import { useAction } from '../useAction';
import { GroupMapField } from './GroupMapField';
import { ResourceField } from './ResourceField';
import { TargetField } from './TargetField';
import { WebFields } from './WebFields';
import { webProblem } from './webOptions';

const ASKS_IN_CHAT = '它追问时直接在 IM 里回答';

/** 回答方式下面那句说明：内置两种写机器人里的特点，扩展的用它自己的说明 */
function modeHelp(mode: ChannelAnswerMode | undefined, member: boolean, web: boolean): string {
  if (!mode) return '原来选的回答方式现在用不了，请重新选';
  if (web) {
    // 能用什么由回答方式定，嵌入不另加限制；访客也能用到命令、文件这些，公开给外部用要心里有数
    if (mode.id === 'agent')
      return '能查资料、多步完成任务；访客也能让它执行命令、读写文件，公开给外部用的建议选快速问答这类只查资料的回答方式';
    if (mode.id === 'quick') return '只查知识库作答，回答更快';
    return mode.description;
  }
  if (mode.id === 'agent')
    return member
      ? `能查资料、多步完成任务；${ASKS_IN_CHAT}，需要人工确认时私聊里给网页链接接着确认，群里会停下说明`
      : `能查资料、多步完成任务；${ASKS_IN_CHAT}，需要人工确认的操作机器人做不了，会停下说明`;
  if (mode.id === 'quick') return '只查知识库作答，回答更快';
  return `${mode.description}；${ASKS_IN_CHAT}，结果附前几行和口径，完整的在网页里看`;
}

function initial(channel?: Channel, preset?: ChannelTarget): ChannelInput {
  return {
    name: channel?.name ?? preset?.name ?? '',
    platform: channel?.platform ?? (preset ? 'web' : 'wecom'),
    enabled: channel?.enabled ?? true,
    botId: channel?.botId ?? '',
    botName: channel?.botName ?? '',
    secret: '',
    settings: channel?.settings ?? {
      answerMode: preset?.answerMode || 'agent',
      bases: [],
      resources: [],
      identitySource: null,
      autoCreate: true,
      groupMap: {},
      target: preset?.ref ?? null,
    },
    audience: channel?.audience ?? 'public',
    web: channel?.web ?? DEFAULT_WEB_OPTIONS,
  };
}

const AUDIENCES: readonly { readonly value: ChannelAudience; readonly label: string }[] = [
  { value: 'public', label: '不认人' },
  { value: 'member', label: '认出是谁' },
];

/** 网页嵌入的访问方式：公开访问访客不用登录；认证访问由接入方系统告诉我们来的是谁（签 JWT） */
const WEB_ACCESS: readonly { readonly value: ChannelAudience; readonly label: string }[] = [
  { value: 'public', label: '公开访问' },
  { value: 'member', label: '认证访问' },
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
  if (input.platform === 'web') {
    const found = webProblem(input.web);
    if (found) return found;
    if (input.settings.target) return undefined; // 挂了对象：回答方式与资料跟着它（服务端再核对对象在不在、选没选资料）
    if (!mode) return '请选回答方式';
    if (input.audience === 'member') return undefined; // 认证访问按本人权限，资料可以不选
    const chosen = mode.resourceType === KNOWLEDGE_RESOURCE ? input.settings.bases : input.settings.resources;
    return chosen.length === 0 ? `请至少选一个${mode.resourceLabel}` : undefined;
  }
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
  preset,
  onClose,
}: {
  readonly channel?: Channel;
  /** 从对象自己的页面（例如专家的「嵌入网页」）打开：平台定为网页嵌入，挂的对象定死 */
  readonly preset?: ChannelTarget;
  readonly onClose: () => void;
}) {
  const editing = channel !== undefined;
  const [input, setInput] = useState<ChannelInput>(() => initial(channel, preset));
  const modes = useQuery({ queryKey: [...KEYS.channels, 'answer-modes'], queryFn: listChannelAnswerModes });
  const targets = useQuery({
    queryKey: [...KEYS.channels, 'targets'],
    queryFn: listChannelTargets,
    enabled: input.platform === 'web' && !preset,
  });
  const sources = useQuery({
    queryKey: [...KEYS.channels, 'identity-sources', input.platform],
    queryFn: () => listIdentitySources(input.platform),
  });
  const sourceList = sources.data ?? [];
  // 没有可选的组织来源时不能切到认人；已经是认人的照样显示，好改回去
  const canRecognize = sourceList.length > 0 || input.audience === 'member';
  const member = input.audience === 'member';
  const web = input.platform === 'web';
  const settings = input.settings;
  const targetList = targets.data ?? [];
  // 挂了对象就照它答：不再选回答方式与资料
  const followed = web && settings.target !== null;
  const showTarget = web && (preset !== undefined || targetList.length > 0 || followed);
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
      success:
        input.platform === 'web'
          ? '已保存，在「嵌入代码」里复制'
          : editing
            ? '已保存，机器人会按新配置重新连接'
            : '已添加，机器人正在连接',
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
      title={editing ? `修改渠道 · ${platform.label}` : preset ? '添加网页嵌入' : '添加渠道'}
      size="md"
      onClose={onClose}
      onSubmit={() => void submit()}
      footer={<DialogActions okText="保存" loading={action.pending} onCancel={onClose} />}
    >
      {editing || preset ? null : (
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
      {web ? null : (
        <>
          <div className="kw-field-row kw-field-row-narrow">
            <Field
              label={platform.idLabel}
              error={
                looksLikeAgentId(input)
                  ? '这像是自建应用的 AgentId，智能机器人的 Bot ID 不是纯数字'
                  : undefined
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
        </>
      )}
      {showTarget ? (
        <TargetField
          value={settings.target}
          targets={targetList}
          locked={preset}
          loading={targets.isPending}
          member={member}
          onChange={(target) =>
            patchSettings(
              target
                ? { target: target.ref, answerMode: target.answerMode || settings.answerMode }
                : { target: null },
            )
          }
        />
      ) : null}
      {followed ? null : (
        <Field label="回答方式" help={modes.isPending && !mode ? undefined : modeHelp(mode, member, web)}>
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
      )}
      {web ? (
        <Field
          label="访问方式"
          help={
            member
              ? '接入方系统（例如你们的 BI）告诉我们来的是谁：它的后端用认证密钥签一个登录信息交给页面，按这个人自己的权限回答'
              : followed
                ? '访客不用登录，都用这个入口自己的身份查'
                : '访客不用登录，都用这个入口自己的身份查，能查的就是下面选的资料'
          }
        >
          <RadioGroup<ChannelAudience>
            aria-label="访问方式"
            direction="row"
            value={input.audience}
            onChange={(audience) => patch({ audience })}
          >
            {WEB_ACCESS.map((item) => (
              <RadioGroup.Item key={item.value} value={item.value}>
                {item.label}
              </RadioGroup.Item>
            ))}
          </RadioGroup>
        </Field>
      ) : null}
      {web ? null : (
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
      )}
      {web && member ? (
        <Field
          label="怎么认人"
          help={
            settings.identitySource
              ? '接入方给的用户编号就是这个组织来源里的 ID（例如企业微信的 userid），对上同步来的人，按他自己的权限回答'
              : '用接入方系统自己的用户编号：先按邮箱对上这里的成员，对不上就按下面的设置建外部用户'
          }
        >
          <Select
            block
            value={settings.identitySource ?? ''}
            options={[
              { value: '', label: '接入方系统自己的用户' },
              ...sourceList.map((source) => ({ value: source.id, label: `组织来源：${source.name}` })),
            ]}
            onChange={(chosen) => patchSettings({ identitySource: chosen.value || null })}
          />
        </Field>
      ) : null}
      {web && member && !settings.identitySource ? (
        <>
          <Switch
            label="对不上的人自动建外部用户（没有密码，只能从这个入口进来，按下面的组对照授权）"
            checked={settings.autoCreate}
            onCheckedChange={(autoCreate) => patchSettings({ autoCreate })}
          />
          <GroupMapField value={settings.groupMap} onChange={(groupMap) => patchSettings({ groupMap })} />
        </>
      ) : null}
      {member && !web ? (
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
      {mode && !followed ? (
        <ResourceField mode={mode} member={member} web={web} settings={settings} onChange={patchSettings} />
      ) : null}
      {web ? (
        <WebFields
          value={input.web}
          onChange={(next) => setInput((current) => ({ ...current, web: { ...current.web, ...next } }))}
        />
      ) : null}
      {editing ? (
        <Switch label="启用" checked={input.enabled} onCheckedChange={(enabled) => patch({ enabled })} />
      ) : null}
    </Dialog>
  );
}
