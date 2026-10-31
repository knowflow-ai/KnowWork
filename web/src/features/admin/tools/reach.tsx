/**
 * 空间服务的「给谁用」：列表里一列、详情里一行（可以改）。企业 MCP 换成「发给」哪些空间，由扩展经范围（scope.ts）给出。
 */
import { useState } from 'react';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import type { McpServer } from '../../../api/mcp';
import { UsersDialog } from '../grants/UsersSelect';
import { useGrantsList, usersOf, usersText, useUsersAction } from '../grants/users';

export function UsersCell({ server }: { readonly server: McpServer }) {
  const grants = useGrantsList();
  return <>{usersText(usersOf(grants.data, `tool:${server.grantPattern}`))}</>;
}

export function UsersRow({ server }: { readonly server: McpServer }) {
  const grants = useGrantsList();
  const saveUsers = useUsersAction();
  const [editing, setEditing] = useState(false);
  const resource = `tool:${server.grantPattern}`;
  const users = usersOf(grants.data, resource);
  return (
    <span className="kw-labeled">
      <span>{usersText(users)}</span>
      <Button color="secondary" variant="ghost" size="sm" onClick={() => setEditing(true)}>
        修改
      </Button>
      {editing ? (
        <UsersDialog
          title={`「${server.title}」给谁用`}
          initial={users}
          pending={saveUsers.pending}
          onSave={(subjects) => saveUsers.run({ resource, subjects })}
          onClose={() => setEditing(false)}
        />
      ) : null}
    </span>
  );
}
