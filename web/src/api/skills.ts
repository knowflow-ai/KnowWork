/**
 * 技能接口（server/src/knowwork_server/skills/api.py、submissions_api.py）：空间技能 /skills 与个人技能 /me/skills。
 * 安装与更新草稿直接发 ZIP 原始字节；更新草稿与发布带 If-Match（草稿修订号）做乐观并发。
 * 个人技能上传即发布；可以提交到空间，空间管理员在 /skill-submissions 审核。
 */
import { request, seg } from './http';
import { bool, isRecord, listOf, num, str, strings, text } from './parse';
import { workspacePath } from './workspace';

export type SkillScope = 'workspace' | 'personal';

const base = (scope: SkillScope, path = '') =>
  workspacePath(`${scope === 'personal' ? '/me/skills' : '/skills'}${path}`);

export interface SkillEnv {
  readonly name: string;
  readonly required: boolean;
  readonly description: string;
}

export interface SkillFile {
  readonly path: string;
  readonly size: number;
  readonly sha256: string;
}

export interface SkillManifest {
  readonly name: string;
  readonly description: string;
  readonly version: string;
  readonly allowedTools: readonly string[];
  readonly completionTools: readonly string[];
  readonly unlocks: readonly string[];
  readonly env: readonly SkillEnv[];
  readonly files: readonly SkillFile[];
}

export type SubmissionStatus = 'pending' | 'approved' | 'rejected' | 'withdrawn';

/** 个人技能提交到空间的一次：版本与清单是提交那一刻的快照。 */
export interface SkillSubmission {
  readonly id: string;
  readonly skillId: string;
  readonly name: string;
  readonly version: string;
  readonly manifest: SkillManifest | null;
  readonly note: string;
  readonly submittedBy: string;
  readonly status: SubmissionStatus;
  readonly reviewNote: string;
  readonly reviewedAt: string | null;
  readonly targetSkillId: string | null;
  readonly createdAt: string | null;
}

/** 我的技能在对话里没生效的原因。 */
export type InactiveReason = 'name_taken' | 'tool_claimed' | 'limit' | 'invalid';

export interface Skill {
  readonly id: string;
  readonly scope: SkillScope;
  readonly name: string;
  readonly description: string;
  readonly status: 'draft' | 'published';
  readonly enabled: boolean;
  readonly draftRevision: number;
  readonly hasUnpublishedChanges: boolean;
  readonly publishedVersion: string | null;
  readonly draft: SkillManifest | null;
  readonly published: SkillManifest | null;
  readonly updatedAt: string | null;
  /** 已经填了值的环境变量（只有名字，值只写不读） */
  readonly envFilled: readonly string[];
  /** 只有「我的技能」列表带：最近一次提交与没生效的原因 */
  readonly submission?: SkillSubmission | null;
  readonly inactive?: InactiveReason | null;
}

export interface SkillRelease {
  readonly id: string;
  readonly version: string;
  readonly releasedBy: string;
  readonly releasedAt: string | null;
}

function parseManifest(value: unknown): SkillManifest | null {
  if (!isRecord(value)) return null;
  return {
    name: str(value.name) ?? '',
    description: str(value.description) ?? '',
    version: str(value.version) ?? '',
    allowedTools: strings(value.allowedTools),
    completionTools: strings(value.completionTools),
    unlocks: strings(value.unlocks),
    env: listOf(value.env, (item) =>
      isRecord(item) && text(item.name)
        ? { name: text(item.name)!, required: bool(item.required), description: str(item.description) ?? '' }
        : undefined,
    ),
    files: listOf(value.files, (item) =>
      isRecord(item) && text(item.path)
        ? { path: text(item.path)!, size: num(item.size) ?? 0, sha256: str(item.sha256) ?? '' }
        : undefined,
    ),
  };
}

const SUBMISSION_STATUS: readonly SubmissionStatus[] = ['pending', 'approved', 'rejected', 'withdrawn'];
const INACTIVE: readonly InactiveReason[] = ['name_taken', 'tool_claimed', 'limit', 'invalid'];

export function parseSubmission(value: unknown): SkillSubmission | undefined {
  if (!isRecord(value)) return undefined;
  const id = text(value.id);
  if (!id) return undefined;
  const status = SUBMISSION_STATUS.find((s) => s === value.status) ?? 'pending';
  return {
    id,
    skillId: str(value.skillId) ?? '',
    name: str(value.name) ?? '',
    version: str(value.version) ?? '',
    manifest: parseManifest(value.manifest),
    note: str(value.note) ?? '',
    submittedBy: str(value.submittedBy) ?? '',
    status,
    reviewNote: str(value.reviewNote) ?? '',
    reviewedAt: text(value.reviewedAt) ?? null,
    targetSkillId: text(value.targetSkillId) ?? null,
    createdAt: text(value.createdAt) ?? null,
  };
}

export function parseSkill(value: unknown): Skill | undefined {
  if (!isRecord(value)) return undefined;
  const id = text(value.id);
  if (!id) return undefined;
  return {
    id,
    scope: value.scope === 'personal' ? 'personal' : 'workspace',
    name: str(value.name) ?? id,
    description: str(value.description) ?? '',
    status: value.status === 'published' ? 'published' : 'draft',
    enabled: bool(value.enabled),
    draftRevision: num(value.draftRevision) ?? 0,
    hasUnpublishedChanges: bool(value.hasUnpublishedChanges),
    publishedVersion: text(value.publishedVersion) ?? null,
    draft: parseManifest(value.draft),
    published: parseManifest(value.published),
    updatedAt: text(value.updatedAt) ?? null,
    envFilled: strings(value.envFilled),
    submission: parseSubmission(value.submission) ?? null,
    inactive: INACTIVE.find((r) => r === value.inactive) ?? null,
  };
}

function parseRelease(value: unknown): SkillRelease | undefined {
  if (!isRecord(value)) return undefined;
  const id = text(value.id);
  const version = text(value.version);
  if (!id || !version) return undefined;
  return { id, version, releasedBy: str(value.releasedBy) ?? '', releasedAt: text(value.releasedAt) ?? null };
}

function required(value: unknown): Skill {
  const skill = parseSkill(value);
  if (!skill) throw new Error('技能数据格式不对');
  return skill;
}

export async function listSkills(scope: SkillScope = 'workspace'): Promise<readonly Skill[]> {
  const body = await request<unknown>(base(scope));
  return listOf(isRecord(body) ? body.skills : undefined, parseSkill);
}

export async function getSkill(id: string, scope: SkillScope = 'workspace') {
  const body = await request<unknown>(base(scope, `/${seg(id)}`));
  return {
    skill: required(body),
    releases: listOf(isRecord(body) ? body.releases : undefined, parseRelease),
  };
}

export async function installSkill(archive: Blob, scope: SkillScope = 'workspace'): Promise<Skill> {
  return required(
    await request<unknown>(base(scope), { method: 'POST', raw: archive, contentType: 'application/zip' }),
  );
}

export async function replaceDraft(skill: Skill, archive: Blob): Promise<Skill> {
  return required(
    await request<unknown>(base(skill.scope, `/${seg(skill.id)}/draft`), {
      method: 'PUT',
      raw: archive,
      contentType: 'application/zip',
      headers: { 'If-Match': String(skill.draftRevision) },
    }),
  );
}

export async function publishSkill(skill: Skill, version?: string): Promise<SkillRelease | undefined> {
  const body = await request<unknown>(base(skill.scope, `/${seg(skill.id)}/publish`), {
    method: 'POST',
    body: version ? { version } : {},
    headers: { 'If-Match': String(skill.draftRevision) },
  });
  return parseRelease(body);
}

export async function setSkillEnabled(skill: Skill, enabled: boolean): Promise<Skill> {
  return required(
    await request<unknown>(base(skill.scope, `/${seg(skill.id)}/${enabled ? 'enable' : 'disable'}`), {
      method: 'POST',
    }),
  );
}

/** 填或清技能的环境变量：名字 → 新值，null 是清掉，没列出的不变。返回的技能只带已填了哪些名字。 */
export async function setSkillEnv(
  skill: Skill,
  values: Readonly<Record<string, string | null>>,
): Promise<Skill> {
  return required(
    await request<unknown>(base(skill.scope, `/${seg(skill.id)}/env`), { method: 'PUT', body: { values } }),
  );
}

/** 技能声明为必填、还没填值的环境变量（看发布版本，没发布过看草稿）。 */
export function missingEnv(skill: Skill): readonly string[] {
  const manifest = skill.published ?? skill.draft;
  return (manifest?.env ?? [])
    .filter((e) => e.required && !skill.envFilled.includes(e.name))
    .map((e) => e.name);
}

export async function deleteSkill(skill: Skill): Promise<void> {
  await request<unknown>(base(skill.scope, `/${seg(skill.id)}`), { method: 'DELETE' });
}

// ---- 我能用的技能 ----

export type SkillSource = 'platform' | 'workspace' | 'personal' | 'bundled';

export interface AvailableSkill {
  readonly name: string;
  readonly description: string;
  readonly source: SkillSource;
  readonly version: string;
}

function parseAvailable(value: unknown): AvailableSkill | undefined {
  if (!isRecord(value)) return undefined;
  const name = text(value.name);
  if (!name) return undefined;
  const source =
    (['workspace', 'personal', 'bundled'] as const).find((s) => s === value.source) ?? 'workspace';
  return { name, description: str(value.description) ?? '', source, version: str(value.version) ?? '' };
}

export async function listAvailableSkills(): Promise<readonly AvailableSkill[]> {
  const body = await request<unknown>(workspacePath('/me/skills/available'));
  return listOf(isRecord(body) ? body.skills : undefined, parseAvailable);
}

// ---- 提交到空间与审核 ----

function requiredSubmission(value: unknown): SkillSubmission {
  const submission = parseSubmission(value);
  if (!submission) throw new Error('提交记录格式不对');
  return submission;
}

export async function submitSkill(skill: Skill, note: string): Promise<SkillSubmission> {
  return requiredSubmission(
    await request<unknown>(base('personal', `/${seg(skill.id)}/submission`), {
      method: 'POST',
      body: { note },
    }),
  );
}

export async function withdrawSubmission(skill: Skill): Promise<void> {
  await request<unknown>(base('personal', `/${seg(skill.id)}/submission`), { method: 'DELETE' });
}

const submissions = (path = '') => workspacePath(`/skill-submissions${path}`);

export async function listPendingSubmissions(): Promise<readonly SkillSubmission[]> {
  const body = await request<unknown>(submissions());
  return listOf(isRecord(body) ? body.submissions : undefined, parseSubmission);
}

/** 空间里已有的那份（之前从同一个个人技能复制来的）当前草稿。 */
export interface SubmissionTarget {
  readonly skillId: string;
  readonly publishedVersion: string | null;
  readonly hasUnpublishedChanges: boolean;
  readonly manifest: SkillManifest | null;
  readonly content: string;
}

export interface SubmissionDetail {
  readonly submission: SkillSubmission;
  readonly content: string;
  readonly target: SubmissionTarget | null;
  readonly nameTaken: boolean;
}

export async function getSubmission(id: string): Promise<SubmissionDetail> {
  const body = await request<unknown>(submissions(`/${seg(id)}`));
  const target = isRecord(body) && isRecord(body.target) ? body.target : undefined;
  return {
    submission: requiredSubmission(body),
    content: isRecord(body) ? (str(body.content) ?? '') : '',
    target: target
      ? {
          skillId: str(target.skillId) ?? '',
          publishedVersion: text(target.publishedVersion) ?? null,
          hasUnpublishedChanges: bool(target.hasUnpublishedChanges),
          manifest: parseManifest(target.manifest),
          content: str(target.content) ?? '',
        }
      : null,
    nameTaken: isRecord(body) && bool(body.nameTaken),
  };
}

/** 提交的技能包下载地址（同源 Cookie 会话，直接用链接下载）。 */
export const submissionArchiveUrl = (id: string): string => submissions(`/${seg(id)}/archive`);

export async function approveSubmission(id: string, publish: boolean): Promise<Skill> {
  return required(
    await request<unknown>(submissions(`/${seg(id)}/approve`), { method: 'POST', body: { publish } }),
  );
}

export async function rejectSubmission(id: string, reason: string): Promise<SkillSubmission> {
  return requiredSubmission(
    await request<unknown>(submissions(`/${seg(id)}/reject`), { method: 'POST', body: { reason } }),
  );
}

// ---- 本空间的企业技能与内置技能（空间管理员停用、收窄给谁用） ----

export interface UpperSkill {
  /** platform:<技能 id> 或 bundled:<名字> */
  readonly key: string;
  readonly name: string;
  readonly description: string;
  readonly source: 'platform' | 'bundled';
  readonly version: string;
  readonly off: boolean;
  /** 收窄了给谁用：只有 resource 上授了「可用」的对象（和管理员）能用；没收窄是全体成员 */
  readonly narrowed: boolean;
  /** 收窄时授权挂的资源（skill:<id> 或 skill:bundled.<名字>），从授权列表里按它找给了谁 */
  readonly resource: string;
}

function parseUpper(value: unknown): UpperSkill | undefined {
  if (!isRecord(value)) return undefined;
  const key = text(value.key);
  const name = text(value.name);
  if (!key || !name) return undefined;
  return {
    key,
    name,
    description: str(value.description) ?? '',
    source: value.source === 'platform' ? 'platform' : 'bundled',
    version: str(value.version) ?? '',
    off: bool(value.off),
    narrowed: bool(value.narrowed),
    resource: str(value.resource) ?? '',
  };
}

export async function listUpperSkills(): Promise<readonly UpperSkill[]> {
  const body = await request<unknown>(workspacePath('/platform-skills'));
  return listOf(isRecord(body) ? body.skills : undefined, parseUpper);
}

/** 改在本空间给谁用：含全体成员（role:editor）就是不收窄。 */
export async function setUpperSkillUsers(key: string, subjects: readonly string[]): Promise<void> {
  await request<unknown>(workspacePath('/platform-skills/users'), { method: 'PUT', body: { key, subjects } });
}

export async function setUpperSkillOff(key: string, off: boolean): Promise<void> {
  await request<unknown>(workspacePath(`/platform-skills/${off ? 'disable' : 'enable'}`), {
    method: 'POST',
    body: { key },
  });
}
