/**
 * 回答方式（智能体 / 快速问答）：放在输入框外面的左上角，一个不带边框的小下拉，点开两项各带一句说明。
 * 原来收在框里的「+」菜单、选了快速问答才在框里出一个标签：用户不点开「+」看不到还有快速问答，现在的模式也只在
 * 选了快速问答时才看得出来。挪到框外常驻显示当前模式，框里只留输入相关的东西。
 * 回答方式是全局的选择，换对话也沿用（stores/answerMode）。
 */
import type { ReactNode } from 'react';
import { Agent, Flash } from '@openai/apps-sdk-ui/components/Icon';
import { Select } from '@openai/apps-sdk-ui/components/Select';
import type { AnswerMode } from '../../../api/conversations';
import { useAnswerMode } from '../../../stores/answerMode';

export const ANSWER_MODE_LABEL = '回答方式';

// 说明包一层自己的样式：组件库把说明放在普通的块里，字号、颜色和模式名一样，选中那项加粗时说明也跟着加粗
const described = (text: string) => <span className="kw-answer-mode-description">{text}</span>;

const MODES: { readonly value: AnswerMode; readonly label: string; readonly description: ReactNode }[] = [
  { value: 'agent', label: '智能体', description: described('能用技能、读文件、执行任务，适合复杂的事') },
  { value: 'quick', label: '快速问答', description: described('只查知识库，回答更快') },
];

export function AnswerModeSelect({ disabled }: { readonly disabled?: boolean }) {
  const mode = useAnswerMode((s) => s.mode);
  const setMode = useAnswerMode((s) => s.setMode);
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
        disabled={disabled}
        TriggerStartIcon={mode === 'quick' ? Flash : Agent}
        options={MODES}
        value={mode}
        onChange={(option) => setMode(option.value)}
      />
    </div>
  );
}
