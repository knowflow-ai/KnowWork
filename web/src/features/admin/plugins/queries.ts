/** 插件页的查询键：预置插件的档、上传的插件（空间插件、成员安装的、管控开关）。 */
export const builtinKey = (workspaceId: string) => ['admin', 'plugins', workspaceId] as const;
export const uploadsKey = (workspaceId: string) => ['admin', 'plugins', workspaceId, 'uploads'] as const;
export const myBuiltinKey = (workspaceId: string) => ['me', 'plugins', workspaceId] as const;
export const myUploadsKey = (workspaceId: string) => ['me', 'plugins', workspaceId, 'uploads'] as const;
export const uploadDetailKey = (workspaceId: string, id: string, admin: boolean) =>
  ['plugins', workspaceId, 'uploads', id, 'detail', admin ? 'admin' : 'me'] as const;
