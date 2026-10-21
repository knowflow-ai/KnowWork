/** 侧栏里的一条会话：点击进入；悬停出现「更多」，可改名、删除（删除需确认）。照 ChatGPT 只写标题、不带图标。 */
import { useState } from 'react';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { DotsHorizontal, EditPencil, Trash } from '@openai/apps-sdk-ui/components/Icon';
import { Input } from '@openai/apps-sdk-ui/components/Input';
import { Menu } from '@openai/apps-sdk-ui/components/Menu';
import { NavLink, useMatch, useNavigate } from 'react-router';
import { MAX_TITLE_LENGTH, validateTitle, type ConversationSummary } from '../../api/conversations';
import { readableError } from '../../api/http';
import { confirm } from '../../ui/confirmStore';
import { Dialog, DialogActions } from '../../ui/Dialog';
import { Field } from '../../ui/Field';
import { toast } from '../../ui/toastStore';
import { useDeleteConversation, useRenameConversation } from './queries';

const linkClass = ({ isActive }: { isActive: boolean }) => `kw-sidebar-link${isActive ? ' is-active' : ''}`;

function RenameDialog({
  item,
  onClose,
}: {
  readonly item: ConversationSummary;
  readonly onClose: () => void;
}) {
  const rename = useRenameConversation();
  const [title, setTitle] = useState(item.title);
  const [problem, setProblem] = useState<string>();
  const submit = async () => {
    const invalid = validateTitle(title);
    setProblem(invalid ?? undefined);
    if (invalid) return;
    try {
      await rename.mutateAsync({ id: item.id, title });
      onClose();
    } catch (error) {
      toast.error(readableError(error, '改名没有成功，请稍后重试'));
    }
  };
  return (
    <Dialog
      title="重命名对话"
      onClose={onClose}
      onSubmit={() => void submit()}
      footer={<DialogActions okText="保存" loading={rename.isPending} onCancel={onClose} />}
    >
      <Field label="名称" error={problem}>
        {(id) => (
          <Input
            id={id}
            value={title}
            maxLength={MAX_TITLE_LENGTH}
            invalid={Boolean(problem)}
            onChange={(e) => setTitle(e.target.value)}
            autoFocus
          />
        )}
      </Field>
    </Dialog>
  );
}

export function ConversationItem({ item }: { readonly item: ConversationSummary }) {
  const navigate = useNavigate();
  const remove = useDeleteConversation();
  const [renaming, setRenaming] = useState(false);
  const to = `/chat/${encodeURIComponent(item.id)}`;
  const isCurrent = useMatch(to) !== null;
  const title = item.title || '未命名对话';

  const confirmDelete = async () => {
    const ok = await confirm({
      title: `删除对话「${title}」？`,
      description: '删除后对话内容与执行记录都无法恢复。',
      okText: '删除',
      danger: true,
    });
    if (!ok) return;
    try {
      await remove.mutateAsync(item.id);
      if (isCurrent) navigate('/chat', { replace: true });
    } catch (error) {
      toast.error(readableError(error, '删除没有成功，请稍后重试'));
    }
  };

  return (
    <div className="kw-conversation-item">
      <NavLink to={to} className={linkClass} title={title}>
        <span className="kw-ellipsis">{title}</span>
      </NavLink>
      <Menu>
        <Menu.Trigger>
          <Button
            color="secondary"
            variant="ghost"
            size="sm"
            uniform
            className="kw-conversation-more"
            aria-label={`更多操作：${title}`}
          >
            <DotsHorizontal />
          </Button>
        </Menu.Trigger>
        <Menu.Content align="start" minWidth={160}>
          <Menu.Item onSelect={() => setRenaming(true)}>
            <EditPencil />
            重命名
          </Menu.Item>
          <Menu.Item className="kw-menu-danger" onSelect={() => void confirmDelete()}>
            <Trash />
            删除
          </Menu.Item>
        </Menu.Content>
      </Menu>
      {renaming ? <RenameDialog item={item} onClose={() => setRenaming(false)} /> : null}
    </div>
  );
}
