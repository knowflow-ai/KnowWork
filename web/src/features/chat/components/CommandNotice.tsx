/**
 * 用户执行的一条 / 命令（照 DSH 官方对话界面的 Command input）：右边是用户敲的那一行，下面一条通知写结果。
 * 命令不经模型，结果是插件直接给的文字：官方 /goal 的换成中文（events/goalText.ts），插件自己的原样显示。
 */
import { Terminal } from '@openai/apps-sdk-ui/components/Icon';
import { commandLine } from '../events/commands';
import { goalCommandText } from '../events/goalText';
import { formatClock } from '../events/selectors';
import type { CommandView } from '../events/types';

const TITLES: Readonly<Record<CommandView['status'], string>> = {
  running: '命令执行中',
  success: '命令已执行',
  error: '命令没能执行',
};

export function CommandNotice({ command }: { readonly command: CommandView }) {
  const line = commandLine(command);
  // 官方 /goal 的结果是英文，换成中文；别的命令（插件自己的）原样显示
  const text = command.text && command.name === 'goal' ? goalCommandText(command.text) : command.text;
  return (
    <>
      <div className="kw-user-row">
        <div className="kw-user-bubble kw-command-line">{line}</div>
      </div>
      <section className="kw-trigger" aria-label={`命令 ${line} 的结果`} role="status">
        <div className="kw-trigger-head kw-command-head">
          <Terminal aria-hidden />
          <span className="kw-trigger-title">{TITLES[command.status]}</span>
          <time dateTime={new Date(command.time).toISOString()}>{formatClock(command.time)}</time>
        </div>
        {text ? (
          <div className="kw-trigger-body">
            <div className="kw-trigger-text">{text}</div>
          </div>
        ) : null}
      </section>
    </>
  );
}
