/**
 * 输入框左下角的「+」与选中的模式标签，照 ChatGPT 的输入框：附件和模式收在同一个「+」菜单里，
 * 默认（智能体）框里什么都不多；选了快速问答，框里出现一个可去掉的标签。
 * 回答方式是全局的选择，换对话也沿用（stores/answerMode）。
 */
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { Flash, PaperclipAttach, PlusComposer, X } from '@openai/apps-sdk-ui/components/Icon';
import { Menu } from '@openai/apps-sdk-ui/components/Menu';
import { Tooltip } from '@openai/apps-sdk-ui/components/Tooltip';
import { useAnswerMode } from '../../../stores/answerMode';

const PLUS_LABEL = '添加文件、快速问答';

interface MenuProps {
  readonly disabled?: boolean;
  readonly onAddFiles: () => void;
}

export function ComposerMenu({ disabled, onAddFiles }: MenuProps) {
  const quick = useAnswerMode((s) => s.mode) === 'quick';
  const setMode = useAnswerMode((s) => s.setMode);
  return (
    <Menu>
      <Tooltip content={PLUS_LABEL} compact>
        <span>
          <Menu.Trigger disabled={disabled}>
            <Button color="secondary" variant="ghost" uniform aria-label={PLUS_LABEL} disabled={disabled}>
              <PlusComposer />
            </Button>
          </Menu.Trigger>
        </span>
      </Tooltip>
      <Menu.Content side="top" align="start" minWidth={240}>
        {/* 快速问答没有读文件的工具，附件放进去也没人读 */}
        <Menu.Item disabled={quick} onSelect={onAddFiles}>
          <PaperclipAttach />
          添加文件
        </Menu.Item>
        <Menu.CheckboxItem
          checked={quick}
          indicatorVariant="ghost"
          onCheckedChange={(checked) => setMode(checked === true ? 'quick' : 'agent')}
        >
          <Flash />
          <span className="kw-menu-item-text">
            快速问答
            <small>只检索知识库，回答更快</small>
          </span>
        </Menu.CheckboxItem>
      </Menu.Content>
    </Menu>
  );
}

/** 选了快速问答时框里的标签：点一下回到智能体（照 ChatGPT 的「Deep research, click to remove」）。 */
export function QuickAnswerChip({ disabled }: { readonly disabled?: boolean }) {
  const quick = useAnswerMode((s) => s.mode) === 'quick';
  const setMode = useAnswerMode((s) => s.setMode);
  if (!quick) return null;
  return (
    <Button
      color="secondary"
      variant="soft"
      size="sm"
      className="kw-mode-chip"
      aria-label="快速问答，点击取消"
      disabled={disabled}
      onClick={() => setMode('agent')}
    >
      <Flash />
      快速问答
      <X className="kw-mode-chip-close" />
    </Button>
  );
}
