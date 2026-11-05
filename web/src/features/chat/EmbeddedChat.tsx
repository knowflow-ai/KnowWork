/**
 * 嵌在别的页面里的会话（例如扩展页面的右栏）：回答方式固定，不显示回答方式下拉；还没有会话时只有输入框，
 * 发第一句时新建会话，先调 onCreated（外层把会话绑到自己的范围），再发出去，之后就是同一个会话视图。
 * 已有会话时传 conversationId 接着聊。还没开始时可以摆几句现成的问题（starters，点了就照这句发出去）和外层给的一块内容
 * （extra，例如不走对话的后台任务按钮）。
 */
import { useState, type ReactNode } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { X } from '@openai/apps-sdk-ui/components/Icon';
import type { AnswerMode } from '../../api/conversations';
import type { Mention } from '../../api/mentions';
import { readableError } from '../../api/http';
import { toast } from '../../ui/toastStore';
import { CONVERSATIONS_KEY } from '../conversations/queries';
import { Composer } from './components/Composer';
import { ConversationView } from './ConversationView';
import type { FirstMessage } from './hooks/useConversation';
import { startConversation } from './startConversation';

interface Props {
  readonly answerMode: AnswerMode;
  readonly conversationId?: string;
  readonly placeholder?: string;
  /** 输入框下面那行小字（见 Composer） */
  readonly footnote?: string;
  /** 还没有会话时输入框上方的标题与一句说明 */
  readonly heading?: string;
  readonly hint?: string;
  /** 还没有会话时摆在说明下面的现成问题：点了就照这句发出去（带上正在看的东西） */
  readonly starters?: readonly string[];
  /** 还没有会话时摆在现成问题下面的一块（外层给，例如后台任务的按钮） */
  readonly extra?: ReactNode;
  readonly onCreated?: (conversationId: string) => Promise<void>;
  /** 每轮跑完时调（见 ConversationView） */
  readonly onTurnEnd?: () => void;
  /**
   * 页面上正在看的东西（例如指标页树里选中的字段）：输入框上方一个小标签写 label，可以点掉；下一条消息开头带上 text，
   * 这条消息里看得见。同一个 key 只附一次，换了 key 再附。
   */
  readonly context?: ChatContext;
}

export interface ChatContext {
  readonly key: string;
  readonly label: string;
  readonly text: string;
}

interface Started {
  readonly id: string;
  readonly firstMessage?: FirstMessage;
}

export function EmbeddedChat({
  answerMode,
  conversationId,
  placeholder,
  footnote,
  heading,
  hint,
  starters = [],
  extra,
  onCreated,
  onTurnEnd,
  context,
}: Props) {
  const client = useQueryClient();
  // 附过或点掉的 key：同一个对象只附一次
  const [settled, setSettled] = useState<string>();
  const pending = context && context.key !== settled ? context : undefined;
  const decorate = (text: string) => {
    if (!pending) return text;
    setSettled(pending.key);
    return `${pending.text}\n${text}`;
  };
  const above = pending ? (
    <div className="kw-chat-context">
      <span>{pending.label}</span>
      <Button
        color="secondary"
        variant="ghost"
        size="3xs"
        uniform
        aria-label="不带上"
        onClick={() => setSettled(pending.key)}
      >
        <X />
      </Button>
    </div>
  ) : null;
  const [started, setStarted] = useState<Started | undefined>(
    conversationId ? { id: conversationId } : undefined,
  );
  if (started) {
    return (
      <ConversationView
        key={started.id}
        conversationId={started.id}
        firstMessage={started.firstMessage}
        answerMode={answerMode}
        placeholder={placeholder}
        footnote={footnote}
        onTurnEnd={onTurnEnd}
        above={above}
        decorate={decorate}
      />
    );
  }
  const start = async (text: string, files: readonly File[], mentions: readonly Mention[]) => {
    try {
      const next = await startConversation({ text: decorate(text), files, mentions, answerMode, onCreated });
      void client.invalidateQueries({ queryKey: CONVERSATIONS_KEY });
      setStarted(next);
    } catch (error) {
      throw new Error(readableError(error, '没能新建对话，请稍后重试'));
    }
  };
  return (
    <div className="kw-chat kw-chat-embedded">
      <div className="kw-chat-embedded-empty">
        <div className="kw-chat-embedded-intro">
          {heading ? <p className="kw-chat-embedded-heading">{heading}</p> : null}
          {hint ? <p>{hint}</p> : null}
          {starters.length > 0 ? (
            <div className="kw-chat-starters" role="group" aria-label="可以这样说">
              {starters.map((text) => (
                <button
                  key={text}
                  type="button"
                  className="kw-chat-starter"
                  onClick={() =>
                    void start(text, [], []).catch((error: unknown) =>
                      toast.error(error instanceof Error ? error.message : '没能新建对话，请稍后重试'),
                    )
                  }
                >
                  {text}
                </button>
              ))}
            </div>
          ) : null}
          {extra}
        </div>
      </div>
      <div className="kw-composer-dock">
        {above}
        <Composer
          running={false}
          answerMode={answerMode}
          placeholder={placeholder}
          footnote={footnote}
          onSend={(text, _mode, files, mentions) => start(text, files, mentions)}
        />
      </div>
    </div>
  );
}
