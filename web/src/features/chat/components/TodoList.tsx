/**
 * 待办清单：这一轮 Agent 用官方 todo_write 维护的任务表，一行摘要「待办 · 已完成 2/5」。
 * 进行中的这一轮默认展开，结束后默认收起（照执行过程的克制做法）；单色，只用图标区分状态。
 */
import { useState } from 'react';
import { CheckCircle, ChevronDown, ChevronRight } from '@openai/apps-sdk-ui/components/Icon';
import { LoadingIndicator } from '@openai/apps-sdk-ui/components/Indicator';
import type { TodoStatus, TodoView } from '../events/types';

interface Props {
  readonly todos: readonly TodoView[];
  /** 这一轮还在执行：进行中的那项显示转圈。 */
  readonly running: boolean;
}

const STATUS_TEXT: Record<TodoStatus, string> = {
  pending: '未开始',
  in_progress: '进行中',
  completed: '已完成',
};

function StatusIcon({ status, running }: { readonly status: TodoStatus; readonly running: boolean }) {
  if (status === 'completed') return <CheckCircle aria-hidden />;
  if (status === 'in_progress' && running) return <LoadingIndicator size={14} aria-hidden />;
  return <span className="kw-todo-dot" aria-hidden />;
}

export function TodoList({ todos, running }: Props) {
  const [open, setOpen] = useState<boolean>();
  if (todos.length === 0) return null;
  const expanded = open ?? running;
  const done = todos.filter((todo) => todo.status === 'completed').length;
  return (
    <div className="kw-todo">
      <button
        type="button"
        className="kw-process-toggle"
        aria-expanded={expanded}
        onClick={() => setOpen(!expanded)}
      >
        <span>
          待办 · 已完成 {done}/{todos.length}
        </span>
        {expanded ? <ChevronDown /> : <ChevronRight />}
      </button>
      {expanded ? (
        <ol className="kw-todo-list" aria-label="待办">
          {todos.map((todo, index) => (
            <li
              // 官方整表替换、条目没有 id；同一轮里内容不重复（官方校验），序号加内容足够稳定
              key={`${index}-${todo.content}`}
              className={`kw-todo-item is-${todo.status}`}
              aria-label={`${todo.content}（${STATUS_TEXT[todo.status]}）`}
            >
              <StatusIcon status={todo.status} running={running} />
              <span className={todo.status === 'in_progress' && running ? 'kw-shimmer' : undefined}>
                {todo.content}
              </span>
            </li>
          ))}
        </ol>
      ) : null}
    </div>
  );
}
