/**
 * 插件（server/src/knowwork_server/plugins/api.py）：DSH 宿主里的官方能力，按空间、按人开关。
 * 管理员给每个插件定档：关闭、全员启用、可选（成员自己决定开不开）；成员在可选的里面自己开关。
 * 改完以后新开的对话和下一次恢复的对话生效。
 */
import { parseCommands, type ConversationCommand } from './conversations';
import { request } from './http';
import { bool, isRecord, listOf as parseList, str, strings } from './parse';
import { workspacePath } from './workspace';

export type PluginMode = 'off' | 'on' | 'optional';

export const PLUGIN_MODES: readonly { readonly value: PluginMode; readonly label: string }[] = [
  { value: 'off', label: '关闭' },
  { value: 'on', label: '全员启用' },
  { value: 'optional', label: '可选' },
];

/**
 * 插件详情里组件清单的一行（照 DSH 官方插件页）：插件包里装的一个插件。预置的是它用到的官方包，标题和说明照抄官方的
 * 中文；上传的是包里配置补丁插入的那几行，包本身用作者写的标题和说明，别的只有模块名。
 */
export interface PluginComponent {
  readonly module: string;
  readonly title: string;
  readonly description: string;
}

function componentsOf(value: unknown): readonly PluginComponent[] {
  return parseList(value, (item) => {
    if (!isRecord(item)) return undefined;
    const module = str(item.module);
    return module
      ? { module, title: str(item.title) || module, description: str(item.description) ?? '' }
      : undefined;
  });
}

/** 管理员看到的一行 */
export interface WorkspacePlugin {
  readonly key: string;
  readonly name: string;
  readonly description: string;
  readonly mode: PluginMode;
  /** 插件详情里的说明文档（Markdown） */
  readonly detail: string;
  readonly components: readonly PluginComponent[];
}

/** 成员看到的一行：locked 是管理员定了全员启用，自己不能关 */
export interface MyPlugin {
  readonly key: string;
  readonly name: string;
  readonly description: string;
  readonly enabled: boolean;
  readonly locked: boolean;
  /** 这个插件带的 / 命令（还没建对话的新对话页靠它列命令） */
  readonly commands: readonly ConversationCommand[];
  readonly detail: string;
  readonly components: readonly PluginComponent[];
}

const modeOf = (value: unknown): PluginMode =>
  PLUGIN_MODES.find((mode) => mode.value === value)?.value ?? 'off';

function listOf<T>(body: unknown, item: (value: Record<string, unknown>) => T | undefined): readonly T[] {
  const raw = isRecord(body) && Array.isArray(body.plugins) ? body.plugins : [];
  return raw.flatMap((value: unknown) => {
    const parsed = isRecord(value) ? item(value) : undefined;
    return parsed ? [parsed] : [];
  });
}

function workspacePluginOf(r: Record<string, unknown>): WorkspacePlugin | undefined {
  const key = str(r.key);
  if (!key) return undefined;
  return {
    key,
    name: str(r.name) || key,
    description: str(r.description) ?? '',
    mode: modeOf(r.mode),
    detail: str(r.detail) ?? '',
    components: componentsOf(r.components),
  };
}

function myPluginOf(r: Record<string, unknown>): MyPlugin | undefined {
  const key = str(r.key);
  if (!key) return undefined;
  return {
    key,
    name: str(r.name) || key,
    description: str(r.description) ?? '',
    enabled: bool(r.enabled),
    locked: bool(r.locked),
    commands: parseCommands(r.commands),
    detail: str(r.detail) ?? '',
    components: componentsOf(r.components),
  };
}

export async function listWorkspacePlugins(): Promise<readonly WorkspacePlugin[]> {
  return listOf(await request<unknown>(workspacePath('/plugins')), workspacePluginOf);
}

export async function setPluginMode(key: string, mode: PluginMode): Promise<void> {
  await request<unknown>(workspacePath(`/plugins/${encodeURIComponent(key)}`), {
    method: 'PUT',
    body: { mode },
  });
}

export async function listMyPlugins(): Promise<readonly MyPlugin[]> {
  return listOf(await request<unknown>(workspacePath('/me/plugins')), myPluginOf);
}

export async function switchMyPlugin(key: string, enabled: boolean): Promise<void> {
  await request<unknown>(workspacePath(`/me/plugins/${encodeURIComponent(key)}`), {
    method: 'PUT',
    body: { enabled },
  });
}

// ---- 上传的插件：空间插件（管理员传、授权给人）与个人插件（成员自己传） ----

/** 适用于：cloud 云端对话、device 本机对话（桌面端） */
export type PluginTarget = 'cloud' | 'device';
export const PLUGIN_TARGETS: readonly { readonly value: PluginTarget; readonly label: string }[] = [
  { value: 'cloud', label: '云端对话' },
  { value: 'device', label: '本机对话' },
];

/** 空间插件的档：传完默认关闭 */
export const UPLOAD_MODES: readonly { readonly value: PluginMode; readonly label: string }[] = [
  { value: 'off', label: '关闭' },
  { value: 'on', label: '默认启用' },
  { value: 'optional', label: '可选' },
];

/** 静态扫描出的能力（只提示）：键 → 给人看的说法 */
export const CAPABILITY_LABELS: Readonly<Record<string, string>> = {
  fs: '读写文件',
  process: '执行命令',
  network: '联网',
  env: '读环境变量',
  'llm-stream': '监听模型调用',
};

export const PLUGIN_ARCHIVE_MAX_BYTES = 100 * 1024 * 1024;

/** 上传的插件的试装状态（开了云端插件运行环境才试装；没开时一律 ready） */
export type UploadStatus = 'checking' | 'ready' | 'failed';

const statusOf = (value: unknown): UploadStatus =>
  value === 'checking' || value === 'failed' ? value : 'ready';

export interface UploadedPlugin {
  readonly id: string;
  /** 包名 */
  readonly name: string;
  /** 作者在语言文件里写的标题；没写为空，界面上显示包名 */
  readonly title: string;
  readonly version: string;
  readonly description: string;
  readonly components: readonly PluginComponent[];
  readonly scope: 'workspace' | 'personal';
  /** 个人插件的属主；空间插件为空 */
  readonly owner: string;
  readonly mode: PluginMode;
  readonly targets: readonly PluginTarget[];
  readonly capabilities: readonly string[];
  /** checking 试装中、ready 可用、failed 没通过（原因在 reason）；只有可用的生效 */
  readonly status: UploadStatus;
  readonly reason: string;
  /** 试装时看到它新增的工具 */
  readonly tools: readonly string[];
  /** 空间插件「给谁用」收窄了没有，收窄后给了谁 */
  readonly narrowed: boolean;
  readonly subjects: readonly string[];
  /** 包名在禁用清单里 */
  readonly blocked: boolean;
  /** 个人插件：本人已经提交给管理员 */
  readonly submitted: boolean;
}

export interface PluginSettings {
  readonly allowPersonal: boolean;
  readonly allowPersonalDevice: boolean;
  readonly blocked: readonly string[];
}

export interface WorkspaceUploads {
  readonly plugins: readonly UploadedPlugin[];
  readonly memberPlugins: readonly UploadedPlugin[];
  readonly settings: PluginSettings;
  /** 这个部署开没开个人专属宿主：没开时上传的插件只在本机对话（桌面端）里生效 */
  readonly cloudAvailable: boolean;
}

/** 「我的插件」里上传的一行 */
export interface MyUpload {
  readonly id: string;
  readonly name: string;
  readonly title: string;
  readonly version: string;
  readonly description: string;
  readonly components: readonly PluginComponent[];
  readonly scope: 'workspace' | 'personal';
  readonly targets: readonly PluginTarget[];
  readonly capabilities: readonly string[];
  readonly status: UploadStatus;
  readonly reason: string;
  readonly tools: readonly string[];
  readonly enabled: boolean;
  readonly locked: boolean;
  readonly blocked: boolean;
  /** 自己装的：已经提交给管理员 */
  readonly submitted: boolean;
}

export interface MyUploads {
  readonly plugins: readonly MyUpload[];
  readonly canInstall: boolean;
  readonly canInstallDevice: boolean;
  readonly cloudAvailable: boolean;
}

const targetsOf = (value: unknown): readonly PluginTarget[] =>
  PLUGIN_TARGETS.map((target) => target.value).filter((target) => strings(value).includes(target));

function uploadedOf(value: unknown): UploadedPlugin | undefined {
  if (!isRecord(value)) return undefined;
  const id = str(value.id);
  const name = str(value.name);
  if (!id || !name) return undefined;
  return {
    id,
    name,
    title: str(value.title) ?? '',
    version: str(value.version) ?? '',
    description: str(value.description) ?? '',
    components: componentsOf(value.components),
    scope: value.scope === 'personal' ? 'personal' : 'workspace',
    owner: str(value.owner) ?? '',
    mode: modeOf(value.mode),
    targets: targetsOf(value.targets),
    capabilities: strings(value.capabilities),
    status: statusOf(value.status),
    reason: str(value.reason) ?? '',
    tools: strings(value.tools),
    narrowed: bool(value.narrowed),
    subjects: strings(value.subjects),
    blocked: bool(value.blocked),
    submitted: bool(value.submitted),
  };
}

function myUploadOf(value: unknown): MyUpload | undefined {
  if (!isRecord(value)) return undefined;
  const id = str(value.id);
  const name = str(value.name);
  if (!id || !name) return undefined;
  return {
    id,
    name,
    title: str(value.title) ?? '',
    version: str(value.version) ?? '',
    description: str(value.description) ?? '',
    components: componentsOf(value.components),
    scope: value.scope === 'personal' ? 'personal' : 'workspace',
    targets: targetsOf(value.targets),
    capabilities: strings(value.capabilities),
    status: statusOf(value.status),
    reason: str(value.reason) ?? '',
    tools: strings(value.tools),
    enabled: bool(value.enabled),
    locked: bool(value.locked),
    blocked: bool(value.blocked),
    submitted: bool(value.submitted),
  };
}

function settingsOf(value: unknown): PluginSettings {
  const r = isRecord(value) ? value : {};
  return {
    allowPersonal: bool(r.allow_personal, true),
    allowPersonalDevice: bool(r.allow_personal_device, true),
    blocked: strings(r.blocked),
  };
}

const targetQuery = (targets: readonly PluginTarget[]) => `?targets=${targets.join(',')}`;

export async function listWorkspaceUploads(): Promise<WorkspaceUploads> {
  const body = await request<unknown>(workspacePath('/plugins/uploads'));
  const r = isRecord(body) ? body : {};
  return {
    plugins: parseList(r.plugins, uploadedOf),
    memberPlugins: parseList(r.member_plugins, uploadedOf),
    settings: settingsOf(r.settings),
    cloudAvailable: bool(r.cloud_available),
  };
}

export async function uploadWorkspacePlugin(archive: Blob, targets: readonly PluginTarget[]): Promise<void> {
  await request<unknown>(workspacePath(`/plugins/uploads${targetQuery(targets)}`), {
    method: 'POST',
    raw: archive,
    contentType: 'application/gzip',
  });
}

export async function updateWorkspacePlugin(
  id: string,
  changes: {
    readonly mode?: PluginMode;
    readonly targets?: readonly PluginTarget[];
    readonly subjects?: readonly string[];
  },
): Promise<void> {
  await request<unknown>(workspacePath(`/plugins/uploads/${encodeURIComponent(id)}`), {
    method: 'PUT',
    body: changes,
  });
}

export async function deleteWorkspacePlugin(id: string): Promise<void> {
  await request<unknown>(workspacePath(`/plugins/uploads/${encodeURIComponent(id)}`), { method: 'DELETE' });
}

export async function savePluginSettings(settings: PluginSettings): Promise<void> {
  await request<unknown>(workspacePath('/plugins/settings'), {
    method: 'PUT',
    body: {
      allow_personal: settings.allowPersonal,
      allow_personal_device: settings.allowPersonalDevice,
      blocked: settings.blocked,
    },
  });
}

export async function listMyUploads(): Promise<MyUploads> {
  const body = await request<unknown>(workspacePath('/me/plugins/uploads'));
  const r = isRecord(body) ? body : {};
  return {
    plugins: parseList(r.plugins, myUploadOf),
    canInstall: bool(r.can_install, true),
    canInstallDevice: bool(r.can_install_device, true),
    cloudAvailable: bool(r.cloud_available),
  };
}

export async function uploadMyPlugin(archive: Blob, targets: readonly PluginTarget[]): Promise<void> {
  await request<unknown>(workspacePath(`/me/plugins/uploads${targetQuery(targets)}`), {
    method: 'POST',
    raw: archive,
    contentType: 'application/gzip',
  });
}

export async function switchMyUpload(id: string, enabled: boolean): Promise<void> {
  await request<unknown>(workspacePath(`/me/plugins/uploads/${encodeURIComponent(id)}`), {
    method: 'PUT',
    body: { enabled },
  });
}

export async function deleteMyPlugin(id: string): Promise<void> {
  await request<unknown>(workspacePath(`/me/plugins/uploads/${encodeURIComponent(id)}`), {
    method: 'DELETE',
  });
}

/** 把我装的个人插件提交给管理员：管理员可以把它转成空间插件、授权给别人 */
export async function submitMyPlugin(id: string): Promise<void> {
  await request<unknown>(workspacePath(`/me/plugins/uploads/${encodeURIComponent(id)}/submit`), {
    method: 'POST',
  });
}

/**
 * 上传插件的说明文档（上传时从包里读的 Markdown）。admin：插件管理页，能看空间里的任何一个；
 * 否则走「我的插件」，只能看给了我的和我自己装的。
 */
export async function fetchUploadDetail(id: string, admin: boolean): Promise<string> {
  const base = admin ? '/plugins/uploads' : '/me/plugins/uploads';
  const body = await request<unknown>(workspacePath(`${base}/${encodeURIComponent(id)}/detail`));
  return (isRecord(body) && str(body.detail)) || '';
}

/** 管理员把成员的个人插件转成空间插件（同一份压缩包，转完默认关闭） */
export async function adoptMemberPlugin(id: string): Promise<void> {
  await request<unknown>(workspacePath(`/plugins/uploads/${encodeURIComponent(id)}/adopt`), {
    method: 'POST',
  });
}

/** 新对话页的 / 命令：自己开着的预置插件带的那些（建了对话以后以执行的一方列的为准） */
export async function listNewChatCommands(): Promise<readonly ConversationCommand[]> {
  const seen = new Set<string>();
  return (await listMyPlugins())
    .filter((plugin) => plugin.enabled)
    .flatMap((plugin) => plugin.commands)
    .filter((command) => !seen.has(command.name) && seen.add(command.name) !== undefined);
}
