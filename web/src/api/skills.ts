/**
 * 技能接口（server/src/knowwork_server/skills/api.py）：空间技能 /skills 与个人技能 /me/skills。
 * 安装与更新草稿直接发 ZIP 原始字节；更新草稿与发布带 If-Match（草稿修订号）做乐观并发。
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

export interface SkillManifest {
  readonly name: string;
  readonly description: string;
  readonly version: string;
  readonly allowedTools: readonly string[];
  readonly completionTools: readonly string[];
  readonly env: readonly SkillEnv[];
}

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
    env: listOf(value.env, (item) =>
      isRecord(item) && text(item.name)
        ? { name: text(item.name)!, required: bool(item.required), description: str(item.description) ?? '' }
        : undefined,
    ),
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

export async function deleteSkill(skill: Skill): Promise<void> {
  await request<unknown>(base(skill.scope, `/${seg(skill.id)}`), { method: 'DELETE' });
}
