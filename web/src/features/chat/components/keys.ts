import type { KeyboardEvent } from 'react';
import type { SendMode } from '../../../api/conversations';

/** 按键决定发送方式；返回 null 表示这个按键不触发发送。 */
export function modeForKey(
  event: Pick<KeyboardEvent, 'key' | 'shiftKey' | 'ctrlKey' | 'metaKey'> & { isComposing?: boolean },
  running: boolean,
): SendMode | null {
  if (event.key !== 'Enter' || event.shiftKey || event.isComposing) return null;
  if ((event.ctrlKey || event.metaKey) && running) return 'steer';
  return 'followup';
}
