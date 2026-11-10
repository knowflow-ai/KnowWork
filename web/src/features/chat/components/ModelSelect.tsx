/**
 * 对话模型：输入框右下角、发送按钮左边的小下拉（照 DSH 官方输入框的模型位：向上展开、右边对齐）。
 * 列出启用的对话模型，名字用管理员起的别名，下面一行灰字是上游模型名、能不能看图。只有一个模型时只显示、不能换。
 * 选择按对话记（stores/chatModel）：默认是这段对话上次用的，新对话用这个人上次用的，都没有用默认模型；发消息时带上，
 * 服务端核对后交给宿主按官方会话级选择换模型（从下一步生效）。
 */
import { useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Select } from '@openai/apps-sdk-ui/components/Select';
import { listChatModels, type ChatModelOption } from '../../../api/conversations';
import { NEW_CHAT, selectedChatModel, useChatModel } from '../../../stores/chatModel';
import { useWorkspaceStore } from '../../../stores/workspace';

export const CHAT_MODEL_LABEL = '对话模型';
const STALE_MS = 60_000;

function describe(model: ChatModelOption) {
  const parts = [model.name !== model.alias ? model.name : '', model.vision ? '能看图' : ''].filter(Boolean);
  return parts.length > 0 ? (
    <span className="kw-answer-mode-description">{parts.join(' · ')}</span>
  ) : undefined;
}

export function ModelSelect({
  conversationId,
  disabled,
}: {
  /** 已有会话的 id；新对话不给 */
  readonly conversationId?: string;
  readonly disabled?: boolean;
}) {
  const key = conversationId ?? NEW_CHAT;
  const workspaceId = useWorkspaceStore((s) => s.id);
  const { data } = useQuery({
    queryKey: ['chat-models', workspaceId, key],
    queryFn: () => listChatModels(conversationId),
    staleTime: STALE_MS,
  });
  const selected = useChatModel((s) => selectedChatModel(s, key));
  const choose = useChatModel((s) => s.choose);
  const setDefault = useChatModel((s) => s.setDefault);
  useEffect(() => {
    if (data) setDefault(key, data.selected);
  }, [data, key, setDefault]);
  const first = data?.items[0];
  if (!data || first === undefined) return null;
  const value = data.items.some((m) => m.alias === selected)
    ? (selected as string)
    : (data.selected ?? first.alias);
  return (
    // 组件库的 Select 不转发 aria-label：包一层以「对话模型」命名的 group，读屏与测试都按它找
    <div role="group" aria-label={CHAT_MODEL_LABEL} className="kw-model-select">
      <Select
        variant="ghost"
        size="sm"
        block={false}
        side="top"
        align="end"
        listMinWidth={220}
        disabled={disabled || data.items.length < 2}
        options={data.items.map((m) => ({ value: m.alias, label: m.alias, description: describe(m) }))}
        value={value}
        onChange={(option) => choose(key, option.value)}
      />
    </div>
  );
}
