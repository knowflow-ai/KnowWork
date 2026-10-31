import { describe, expect, it } from 'vitest';
import type { Skill, SkillSubmission } from '../../api/skills';
import { canSubmit, filterByKeyword, hasPending, mySkillState, submissionState } from './model';

const skill = (patch: Partial<Skill> = {}): Skill => ({
  id: 's1',
  scope: 'personal',
  name: 'my-notes',
  description: '会议纪要',
  status: 'published',
  enabled: true,
  draftRevision: 1,
  hasUnpublishedChanges: false,
  publishedVersion: '1.0.0',
  draft: null,
  published: null,
  updatedAt: null,
  envFilled: [],
  submission: null,
  inactive: null,
  ...patch,
});

const submission = (patch: Partial<SkillSubmission> = {}): SkillSubmission => ({
  id: 'sub1',
  skillId: 's1',
  name: 'my-notes',
  version: '1.0.0',
  manifest: null,
  note: '',
  submittedBy: 'u1',
  status: 'pending',
  reviewNote: '',
  reviewedAt: null,
  targetSkillId: null,
  createdAt: null,
  ...patch,
});

describe('我的技能的状态', () => {
  it('必填的环境变量还没填时提示，用到时会出错', () => {
    const published = {
      name: 'my-notes',
      description: '',
      version: '',
      allowedTools: [],
      completionTools: [],
      unlocks: [],
      files: [],
      env: [{ name: 'REPORT_API_KEY', required: true, description: '' }],
    };
    expect(mySkillState(skill({ published })).note).toBe('还没填环境变量 REPORT_API_KEY，用到时会出错');
    expect(mySkillState(skill({ published, envFilled: ['REPORT_API_KEY'] }))).toEqual({ text: '已启用' });
  });

  it('停用优先，其次写没生效的原因', () => {
    expect(mySkillState(skill())).toEqual({ text: '已启用' });
    expect(mySkillState(skill({ enabled: false, inactive: 'name_taken' }))).toEqual({ text: '已停用' });
    expect(mySkillState(skill({ inactive: 'name_taken' }))).toEqual({
      text: '没生效',
      note: '有同名的空间技能或企业技能，对话里用的是那一份',
    });
  });

  it('提交进度：待审、已进空间（有新版本时提示可再提交）、未通过带原因', () => {
    expect(submissionState(skill())).toBeUndefined();
    expect(submissionState(skill({ submission: submission() }))?.text).toBe('待审核');
    expect(submissionState(skill({ submission: submission({ status: 'approved' }) }))).toEqual({
      text: '已进空间',
      note: '1.0.0',
    });
    expect(
      submissionState(skill({ publishedVersion: '1.1.0', submission: submission({ status: 'approved' }) }))
        ?.note,
    ).toBe('空间里是 1.0.0，可以提交新版本');
    expect(
      submissionState(skill({ submission: submission({ status: 'rejected', reviewNote: '重复了' }) })),
    ).toEqual({ text: '未通过', note: '重复了' });
  });

  it('这一版在待审或已进空间时不能再提交；有待审才能撤回', () => {
    expect(canSubmit(skill())).toBe(true);
    expect(canSubmit(skill({ submission: submission() }))).toBe(false);
    expect(canSubmit(skill({ publishedVersion: '1.1.0', submission: submission() }))).toBe(true);
    expect(canSubmit(skill({ submission: submission({ status: 'approved' }) }))).toBe(false);
    expect(canSubmit(skill({ submission: submission({ status: 'rejected' }) }))).toBe(true);
    expect(hasPending(skill({ submission: submission() }))).toBe(true);
    expect(hasPending(skill({ submission: submission({ status: 'withdrawn' }) }))).toBe(false);
  });

  it('按名称或说明搜索', () => {
    const rows = [
      { name: 'office-docx', description: 'Word 文档' },
      { name: 'my-notes', description: '会议纪要' },
    ];
    expect(filterByKeyword(rows, '纪要').map((r) => r.name)).toEqual(['my-notes']);
    expect(filterByKeyword(rows, ' ')).toHaveLength(2);
  });
});
