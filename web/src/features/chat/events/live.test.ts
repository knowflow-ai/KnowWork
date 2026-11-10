/**
 * 逐字输出：服务端推来的片段按已有字数接着拼，对不上的不拼（等正式消息）；这一步的正式消息到了、这一轮结束、
 * 收到结束标记时清掉。片段不进事件日志，不影响断线续传的位置。
 */
import { describe, expect, it } from 'vitest';
import { ev } from './fixtures';
import { parseLive } from './live';
import { chatReducer, initialChatState } from './reducer';
import type { ChatState, LivePiece } from './types';

const piece = (offset: number, text: string, step = 1): LivePiece => ({
  runId: 'run-1',
  step,
  offset,
  text,
  tools: [],
});

function feed(state: ChatState, ...pieces: LivePiece[]): ChatState {
  return pieces.reduce((s, live) => chatReducer(s, { type: 'live', live }), state);
}

describe('逐字输出', () => {
  it('片段按已有字数接着拼，对不上的不拼，新一步从头替换', () => {
    const started = chatReducer(initialChatState, { type: 'wire', event: ev.runStarted(1) });
    const typed = feed(started, piece(0, ''), piece(0, '短剧'), piece(2, '规模'), piece(9, '漏了一段'));
    expect(typed.live).toEqual({ runId: 'run-1', step: 1, text: '短剧规模', tools: [], reasoning: '' });
    expect(typed.log).toEqual(started.log);
    expect(typed.lastId).toBe(started.lastId);
    expect(feed(typed, piece(0, '', 2)).live).toEqual({
      runId: 'run-1',
      step: 2,
      text: '',
      tools: [],
      reasoning: '',
    });
    expect(feed(typed, { runId: 'run-1', step: 1, done: true }).live).toBeUndefined();
    expect(feed(typed, { runId: 'run-1', step: 9, done: true }).live).toEqual(typed.live);
  });

  it('这一步的正式消息到了、这一轮结束时清掉', () => {
    const typed = feed(
      chatReducer(initialChatState, { type: 'wire', event: ev.runStarted(1) }),
      piece(0, '短剧'),
    );
    expect(chatReducer(typed, { type: 'wire', event: ev.message(2, '短剧规模增长') }).live).toBeUndefined();
    expect(chatReducer(typed, { type: 'wire', event: ev.runCompleted(2) }).live).toBeUndefined();
    expect(chatReducer(typed, { type: 'wire', event: ev.runStarted(2, 2) }).live).toEqual(typed.live);
  });

  it('模型开始写工具调用时记下工具名（同一个只记一次），快照带着报过的工具', () => {
    const started = feed(
      chatReducer(initialChatState, { type: 'wire', event: ev.runStarted(1) }),
      piece(0, ''),
    );
    const tool = (name: string, step = 1): LivePiece => ({ runId: 'run-1', step, tool: name });
    const writing = feed(started, tool('write'), tool('write'), tool('read', 2));
    expect(writing.live?.tools).toEqual(['write']);
    expect(parseLive({ run_id: 'run-1', step: 1, tool: 'write' })).toEqual(tool('write'));
    expect(parseLive({ run_id: 'run-1', step: 1, offset: 0, text: '', tools: ['write', 3] })).toEqual({
      ...piece(0, ''),
      tools: ['write'],
    });
  });

  it('思考片段按自己的字数接着拼，正文开始后照样保留；开始与快照带着思考就替换，新一步清空', () => {
    const started = feed(
      chatReducer(initialChatState, { type: 'wire', event: ev.runStarted(1) }),
      piece(0, ''),
    );
    const think = (offset: number, text: string, step = 1): LivePiece => ({
      runId: 'run-1',
      step,
      reasoningOffset: offset,
      reasoning: text,
    });
    const thinking = feed(
      started,
      think(0, '先看'),
      think(2, '数据'),
      think(9, '漏了'),
      think(0, '别的步', 2),
    );
    expect(thinking.live?.reasoning).toBe('先看数据');
    const answering = feed(thinking, piece(0, '结论'));
    expect(answering.live).toMatchObject({ text: '结论', reasoning: '先看数据' });
    expect(feed(thinking, { ...piece(0, ''), reasoning: '' }).live?.reasoning).toBe('');
    expect(feed(thinking, piece(0, '', 2)).live?.reasoning).toBe('');
    const reconnected = feed(thinking, { ...piece(0, '结'), reasoning: '先看数据' });
    expect(reconnected.live).toMatchObject({ text: '结', reasoning: '先看数据' });
    expect(feed(reconnected, piece(1, '论')).live).toMatchObject({ text: '结论', reasoning: '先看数据' });
    expect(parseLive({ run_id: 'run-1', step: 1, reasoning_offset: 2, reasoning: '数据' })).toEqual(
      think(2, '数据'),
    );
    expect(parseLive({ run_id: 'run-1', step: 1, offset: 0, text: '', reasoning: '先看' })).toEqual({
      ...piece(0, ''),
      reasoning: '先看',
    });
  });

  it('只认格式对的片段', () => {
    expect(parseLive({ run_id: 'run-1', step: 1, offset: 0, text: '短' })).toEqual(piece(0, '短'));
    expect(parseLive({ run_id: 'run-1', step: 1, done: true })).toEqual({
      runId: 'run-1',
      step: 1,
      done: true,
    });
    for (const bad of [
      null,
      {},
      { run_id: 'run-1', step: '1', offset: 0, text: 'x' },
      { run_id: 1, step: 1, done: true },
      { run_id: 'run-1', step: 1, offset: -1, text: 'x' },
      { run_id: 'run-1', step: 1, offset: 0 },
      { run_id: 'run-1', step: 1, reasoning: 'x', reasoning_offset: -1 },
    ]) {
      expect(parseLive(bad)).toBeUndefined();
    }
  });
});
