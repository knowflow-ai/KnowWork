/**
 * 回答方式（智能体 / 快速问答，以及扩展登记的）：放在输入框外面的左上角，一个不带边框的小下拉，点开每项带一句说明。
 * 原来收在框里的「+」菜单、选了快速问答才在框里出一个标签：用户不点开「+」看不到还有快速问答，现在的模式也只在
 * 选了快速问答时才看得出来。挪到框外常驻显示当前模式，框里只留输入相关的东西。
 * 回答方式是全局的选择，换对话也沿用（stores/answerMode）。可选项按空间从服务端取，取到之前是内置两种。
 */
import { useEffect, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Agent, Flash, PluginPuzzle } from '@openai/apps-sdk-ui/components/Icon';
import { Select } from '@openai/apps-sdk-ui/components/Select';
import { listAnswerModes } from '../../../api/conversations';
import { effectiveAnswerMode, useAnswerMode } from '../../../stores/answerMode';
import { useWorkspaceStore } from '../../../stores/workspace';

export const ANSWER_MODE_LABEL = '回答方式';
const STALE_MS = 5 * 60_000;

// 说明包一层自己的样式：组件库把说明放在普通的块里，字号、颜色和模式名一样，选中那项加粗时说明也跟着加粗
const described = (text: string) => <span className="kw-answer-mode-description">{text}</span>;
// 内置两种各有图标；扩展登记的用同一个插件图标
const ICONS: Record<string, typeof Agent> = { agent: Agent, quick: Flash };

/** locked：固定为这个回答方式（例如在用户电脑上执行的对话只有智能体），照常显示、不可改。 */
export function AnswerModeSelect({
  disabled,
  locked,
}: {
  readonly disabled?: boolean;
  readonly locked?: string;
}) {
  const mode = useAnswerMode((s) => s.mode);
  const options = useAnswerMode((s) => s.options);
  const setMode = useAnswerMode((s) => s.setMode);
  const setOptions = useAnswerMode((s) => s.setOptions);
  const workspaceId = useWorkspaceStore((s) => s.id);
  const { data } = useQuery({
    queryKey: ['answer-modes', workspaceId],
    queryFn: listAnswerModes,
    staleTime: STALE_MS,
  });
  useEffect(() => {
    if (data) setOptions(data);
  }, [data, setOptions]);
  const value = locked ?? effectiveAnswerMode(mode, options);
  const items: { readonly value: string; readonly label: string; readonly description: ReactNode }[] =
    options.map((option) => ({
      value: option.id,
      label: option.label,
      description: described(option.description),
    }));
  return (
    // 组件库的 Select 不转发 aria-label：包一层以「回答方式」命名的 group，读屏与测试都按它找
    <div role="group" aria-label={ANSWER_MODE_LABEL} className="kw-answer-mode">
      <Select
        variant="ghost"
        size="sm"
        block={false}
        opticallyAlign="start"
        align="start"
        listWidth={280}
        disabled={disabled || locked !== undefined}
        TriggerStartIcon={ICONS[value] ?? PluginPuzzle}
        options={items}
        value={value}
        onChange={(option) => setMode(option.value)}
      />
    </div>
  );
}
