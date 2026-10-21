/**
 * 侧栏底部的用户菜单，使用端与管理端共用：个人设置、回答语言（仅使用端）、管理后台（仅管理员、仅使用端）、退出登录。
 * 回答语言照 ChatGPT 放在设置类入口里，不占输入框；所选语言随每条消息带给服务端（明确指定优先）。
 */
import { Avatar } from '@openai/apps-sdk-ui/components/Avatar';
import { BuildingWorkspace, Globe, Logout, Settings } from '@openai/apps-sdk-ui/components/Icon';
import { Menu } from '@openai/apps-sdk-ui/components/Menu';
import { useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router';
import { logout } from '../api/auth';
import { REPLY_LANGUAGES, type ReplyLanguage } from '../api/conversations';
import { useReplyLanguage } from '../stores/replyLanguage';
import { useIsWorkspaceAdmin, useMe } from '../features/auth/queries';
import { toast } from '../ui/toastStore';

export function UserMenu({ inAdmin = false }: { readonly inAdmin?: boolean }) {
  const navigate = useNavigate();
  const client = useQueryClient();
  const me = useMe();
  const admin = useIsWorkspaceAdmin();
  const language = useReplyLanguage((s) => s.language);
  const setLanguage = useReplyLanguage((s) => s.setLanguage);
  const name = me.data?.name ?? '未命名用户';
  const languageLabel = REPLY_LANGUAGES.find((l) => l.value === language)?.label ?? '中文';
  const signOut = async () => {
    try {
      await logout();
    } catch {
      toast.warning('没能通知服务端退出，本机已退出登录');
    }
    client.clear();
    navigate('/login', { replace: true });
  };
  return (
    <div className="kw-sidebar-footer">
      <Menu>
        <Menu.Trigger>
          <button type="button" className="kw-sidebar-link" aria-label="账号菜单">
            <Avatar name={name} size={28} />
            <span className="kw-user-meta">
              <span className="kw-ellipsis">{name}</span>
              <small className="kw-ellipsis">{me.data?.email ?? '默认空间'}</small>
            </span>
          </button>
        </Menu.Trigger>
        <Menu.Content side="top" align="start" minWidth={220}>
          <Menu.Item onSelect={() => navigate('/settings')}>
            <Settings />
            个人设置
          </Menu.Item>
          {inAdmin ? null : (
            <Menu.Sub>
              <Menu.SubTrigger>
                <Globe />
                <span className="kw-menu-with-value">
                  回答语言<span className="kw-menu-value">{languageLabel}</span>
                </span>
              </Menu.SubTrigger>
              <Menu.SubContent minWidth={140}>
                <Menu.RadioGroup<ReplyLanguage> value={language} onChange={setLanguage}>
                  {REPLY_LANGUAGES.map((l) => (
                    <Menu.RadioItem key={l.value} value={l.value}>
                      {l.label}
                    </Menu.RadioItem>
                  ))}
                </Menu.RadioGroup>
              </Menu.SubContent>
            </Menu.Sub>
          )}
          {admin.data && !inAdmin ? (
            <Menu.Item onSelect={() => navigate('/admin')}>
              <BuildingWorkspace />
              管理后台
            </Menu.Item>
          ) : null}
          <Menu.Separator />
          <Menu.Item onSelect={() => void signOut()}>
            <Logout />
            退出登录
          </Menu.Item>
        </Menu.Content>
      </Menu>
    </div>
  );
}
