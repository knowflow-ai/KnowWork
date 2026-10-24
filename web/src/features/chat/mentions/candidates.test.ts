import { describe, expect, it } from 'vitest';
import type { MentionOptions } from '../../../api/mentions';
import {
  activeTrigger,
  expandFolder,
  FLAT_LIMIT,
  groupCandidates,
  sections,
  withoutTrigger,
} from './candidates';

const OPTIONS: MentionOptions = {
  bases: [
    {
      id: 'b.kb1',
      name: '报销制度',
      description: '差旅与报销',
      source: '扩展知识库',
      path: ['财务', '报销'],
      tags: ['简报'],
      pinnable: true,
    },
    {
      id: 'b.kb2',
      name: '预算库',
      description: '',
      source: '扩展知识库',
      path: ['财务'],
      tags: [],
      pinnable: true,
    },
    {
      id: 'k.ds1',
      name: '外部制度',
      description: '',
      source: 'KnowFlow',
      path: [],
      tags: ['不给'],
      pinnable: false,
    },
  ],
  skills: [{ name: 'weekly-report', description: '写周报' }],
};

describe('光标前的 @', () => {
  it.each([
    ['@', 1, { start: 0, query: '' }],
    ['问 @报销', 5, { start: 2, query: '报销' }],
    ['a@b.com', 7, null],
    ['@报 销', 4, null],
    ['@报销 后面', 2, { start: 0, query: '报' }],
  ])('%s（光标 %i）', (value, caret, expected) => expect(activeTrigger(value, caret)).toEqual(expected));

  it('选中后去掉打出来的 @查找词，后面的字留着', () => {
    expect(withoutTrigger('问 @报销 标准', { start: 2, query: '报销' }, 5)).toBe('问  标准');
  });
});

describe('候选', () => {
  it('知识库组先列目录（每一级都能选，带下面的知识库），再列知识库（小字写目录与简介）', () => {
    const items = groupCandidates('bases', OPTIONS, []);
    expect(items.map((c) => [c.mention.type, c.mention.name, c.detail])).toEqual([
      ['folder', '财务', '目录 · 2 个知识库'],
      ['folder', '财务/报销', '目录 · 1 个知识库'],
      ['knowledge_base', '报销制度', '财务/报销 · 差旅与报销'],
      ['knowledge_base', '预算库', '财务'],
      ['knowledge_base', '外部制度', ''],
    ]);
    expect(items[0]?.mention.bases).toEqual(['b.kb1', 'b.kb2']);
  });

  it('标签只给能点名的知识库，带所在知识库', () => {
    expect(groupCandidates('tags', OPTIONS, []).map((c) => c.mention)).toEqual([
      { type: 'tag', id: '简报', name: '简报', base: 'b.kb1', baseName: '报销制度' },
    ]);
  });

  it('直接输入时跨组查，每组露前几项；进了组就是这一组的全部；文件用服务端搜到的，不再筛', () => {
    const many: MentionOptions = {
      ...OPTIONS,
      skills: Array.from({ length: 8 }, (_, i) => ({ name: `report-${i}`, description: '' })),
    };
    const docs = [{ id: 'kb1/d1', title: '差旅办法', base: 'b.kb1', baseName: '报销制度' }];
    const flat = sections(many, docs, 'report', null);
    expect(flat.map((s) => [s.group, s.items.length, s.total])).toEqual([
      ['documents', 1, 1],
      ['skills', FLAT_LIMIT, 8],
    ]);
    expect(sections(many, docs, 'report', 'skills')[0]?.items).toHaveLength(8);
    // 标签按所在知识库的名字也能查到
    expect(sections(OPTIONS, [], '报销', null).map((s) => s.group)).toEqual(['bases', 'tags']);
  });

  it('重新回答时按路径把目录重新展开成下面的知识库', () => {
    expect(expandFolder('财务', OPTIONS.bases)).toEqual(['b.kb1', 'b.kb2']);
    expect(expandFolder('财务/报销', OPTIONS.bases)).toEqual(['b.kb1']);
    expect(expandFolder('人事', OPTIONS.bases)).toEqual([]);
  });
});
