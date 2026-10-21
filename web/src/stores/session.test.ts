import { describe, expect, it } from 'vitest';
import { useSessionStore } from './session';

describe('useSessionStore', () => {
  it('记录会话失效并可复位', () => {
    useSessionStore.getState().expire();
    expect(useSessionStore.getState().expired).toBe(true);
    useSessionStore.getState().reset();
    expect(useSessionStore.getState().expired).toBe(false);
  });
});
