/**
 * 输入框里选中的 / 命令（照 ChatGPT 输入框里选中的工具：一个带 × 的小标签，和 @ 的小标签同一种样子、在同一行）：
 * - 带参数的命令（宿主声明了输入，列表里带参数提示）在 / 菜单里选中、或打完 `/名字` 按空格，就变成小标签，输入框里打的是
 *   参数，占位文字换成参数提示；不带参数的命令选中就执行；
 * - 点 × 去掉；输入框空着时按退格也去掉（打过的参数留着，变回普通文字）；
 * - 回车执行（不发给模型），提交的是登记的名字加参数。标签上写中文名（官方内置命令有），没有就写 /名字。
 */
import { useState, type KeyboardEvent, type ReactNode } from 'react';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { Terminal, X } from '@openai/apps-sdk-ui/components/Icon';
import { Tooltip } from '@openai/apps-sdk-ui/components/Tooltip';
import type { ConversationCommand } from '../../../api/conversations';
import { commandText, resolveCommand } from './slash';

export interface CommandClaim {
  readonly command: ConversationCommand;
}

/** 这个命令认不认领：宿主给了参数提示（声明了输入）的才认领，别的选中就执行 */
export function takesInput(command: ConversationCommand): boolean {
  return command.hint !== undefined;
}

/** 打完 `/名字` 按空格：光标正好在行首这个词的末尾、认得是带参数的命令，就认领它，后面的字留作参数 */
export function claimOnSpace(
  value: string,
  caret: number,
  commands: readonly ConversationCommand[] | undefined,
): (CommandClaim & { readonly rest: string }) | undefined {
  const match = /^\/(\S+)$/.exec(value.slice(0, caret));
  if (!match?.[1] || !commands) return undefined;
  const command = resolveCommand(match[1], commands);
  if (!command || !takesInput(command)) return undefined;
  return { command, rest: value.slice(caret).replace(/^\s+/, '') };
}

/** 认领的命令连同参数写成一行交给执行的一方 */
export function claimedLine(claim: CommandClaim, text: string): string {
  return `/${claim.command.name} ${text.trim()}`.trim();
}

/** 标签上的名字：官方内置命令写中文名，别的写 /名字 */
export function claimName(command: ConversationCommand): string {
  return commandText(command).title ?? `/${command.name}`;
}

export interface CommandClaimState {
  readonly claim: CommandClaim | null;
  /** 认领一个命令；之前的被替换 */
  readonly take: (claim: CommandClaim) => void;
  readonly release: () => void;
  /**
   * 输入框的按键先过这里：打完 `/名字` 按空格认领、输入框空着时退格去掉标签。处理了返回 true（已 preventDefault）。
   * setValue、placeCaret 是输入框改文字、放光标的办法
   */
  readonly onKey: (
    event: KeyboardEvent<HTMLTextAreaElement>,
    commands: readonly ConversationCommand[] | undefined,
    setValue: (value: string) => void,
    placeCaret: (at: number) => void,
  ) => boolean;
  /** 小标签（放在 @ 小标签那一行的最前面）；没认领时是 null */
  readonly chip: ReactNode;
}

export function useCommandClaim(): CommandClaimState {
  const [claim, setClaim] = useState<CommandClaim | null>(null);
  const release = () => setClaim(null);
  const onKey: CommandClaimState['onKey'] = (event, commands, setValue, placeCaret) => {
    if (event.nativeEvent.isComposing) return false;
    const box = event.currentTarget;
    if (claim && event.key === 'Backspace' && box.value === '') {
      event.preventDefault();
      release();
      return true;
    }
    if (claim || event.key !== ' ') return false;
    const claimed = claimOnSpace(box.value, box.selectionStart, commands);
    if (!claimed) return false;
    event.preventDefault();
    setClaim({ command: claimed.command });
    setValue(claimed.rest);
    placeCaret(0);
    return true;
  };
  const name = claim ? claimName(claim.command) : '';
  const chip = claim ? (
    <li className="kw-mention-chip">
      <Tooltip content={commandText(claim.command).detail} compact>
        <span className="kw-mention-chip-body">
          <span className="kw-mention-chip-icon" aria-hidden>
            <Terminal />
          </span>
          <span className="kw-mention-chip-kind">命令</span>
          <span className="kw-mention-chip-name">{name}</span>
        </span>
      </Tooltip>
      <Button
        color="secondary"
        variant="ghost"
        size="2xs"
        uniform
        aria-label={`去掉命令「${name}」`}
        onClick={release}
      >
        <X />
      </Button>
    </li>
  ) : null;
  return { claim, take: setClaim, release, onKey, chip };
}
