/** 对话框确认的状态：confirm() 返回用户是否确认，ConfirmHost 负责显示，同一时间只有一个确认框。 */
import type { ReactNode } from 'react';
import { create } from 'zustand';

export interface ConfirmOptions {
  readonly title: ReactNode;
  readonly description?: ReactNode;
  readonly okText: string;
  readonly danger?: boolean;
}

interface Pending extends ConfirmOptions {
  readonly resolve: (ok: boolean) => void;
}

export const useConfirmState = create<{ readonly pending: Pending | null }>(() => ({ pending: null }));

export function confirm(options: ConfirmOptions): Promise<boolean> {
  return new Promise((resolve) => {
    useConfirmState.getState().pending?.resolve(false);
    useConfirmState.setState({ pending: { ...options, resolve } });
  });
}
