/**
 * 资源授权：谁能以哪一档权限使用哪些资源。不含空间角色自带的默认权限；模型不授权，空间成员都能用。
 * 资源分两类：数据类（知识库、数据源）默认只有管理员，在这里授「使用」
 * 「编辑」「管理」；能力类（技能、MCP 服务）默认全体成员可用，「使用」在各自详情的「给谁用」里改，这里只授技能的编辑、管理。
 * 列表照样列出全部授权（含「给谁用」存下的那些），撤销也在这里。
 */
import { useMemo, useState, type ReactNode } from 'react';
import { Badge } from '@openai/apps-sdk-ui/components/Badge';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { Plus } from '@openai/apps-sdk-ui/components/Icon';
import { useQuery } from '@tanstack/react-query';
import { listGrants, revokeGrant, type Grant } from '../../../api/workspace';
import { AdminPage, Toolbar } from '../AdminPage';
import { RowActions } from '../common';
import { useAction } from '../useAction';
import { includesText } from '../table';
import { SearchInput } from '../../../ui/SearchInput';
import { Skeleton } from '../../../ui/Skeleton';
import { Table, type Column } from '../../../ui/Table';
import { KEYS } from '../queries';
import { GrantDialog } from './GrantDialog';
import { useDirectory } from './directory';
import { actionLabel, resourceLabel, subjectLabel } from './labels';
import { resourceIcon } from './icons';

/** 类型用灰色小标签，名称跟在后面；note 是名称后面的浅色说明（例如资源已失效）。 */
function Labeled({
  kind,
  name,
  icon,
  note,
  title,
}: {
  readonly kind: string;
  readonly name: string;
  readonly icon?: ReactNode;
  readonly note?: string;
  readonly title?: string;
}) {
  return (
    <span className="kw-labeled">
      <Badge color="secondary" variant="soft" size="sm">
        {kind}
      </Badge>
      {icon ? (
        <span className="kw-labeled-icon" aria-hidden>
          {icon}
        </span>
      ) : null}
      <span title={title}>{name}</span>
      {note ? (
        <span className="kw-secondary" title={title}>
          {note}
        </span>
      ) : null}
    </span>
  );
}

export default function GrantsPage() {
  const grants = useQuery({ queryKey: KEYS.grants, queryFn: listGrants });
  const dir = useDirectory();
  const [keyword, setKeyword] = useState('');
  const [adding, setAdding] = useState(false);
  const revoke = useAction((g: Grant) => revokeGrant(g.subject, g.resource), {
    invalidate: [KEYS.grants],
    success: '已撤销授权',
    failure: '撤销没有成功，请稍后重试',
  });
  const subjectOf = (g: Grant) => subjectLabel(g.subject, dir);
  const resourceOf = (g: Grant) => {
    const r = resourceLabel(g.resource, dir);
    return {
      kind: r.type,
      name: r.name,
      icon: resourceIcon(g.resource),
      // 列窄，说明只写原因；完整说明和原来的编号（给排查用）放在悬停提示里
      ...(r.stale
        ? { note: '已删除或共享已撤销', title: `这条授权已不起作用，可以撤销（${g.resource}）` }
        : {}),
    };
  };
  const rows = useMemo(
    () =>
      (grants.data ?? []).filter((g) => {
        const s = subjectLabel(g.subject, dir);
        const r = resourceLabel(g.resource, dir);
        return includesText([s.kind, s.name, r.type, r.name, actionLabel(g.action)], keyword);
      }),
    [grants.data, dir, keyword],
  );

  const columns: readonly Column<Grant>[] = [
    {
      key: 'subject',
      title: '授权对象',
      ellipsis: true,
      render: (g) => <Labeled {...subjectOf(g)} />,
    },
    {
      key: 'resource',
      title: '资源',
      ellipsis: true,
      render: (g) => <Labeled {...resourceOf(g)} />,
    },
    { key: 'action', title: '权限', width: 110, render: (g) => actionLabel(g.action) },
    {
      key: 'actions',
      title: '操作',
      width: 100,
      render: (g) => (
        <RowActions
          actions={[
            {
              key: 'revoke',
              label: '撤销',
              danger: true,
              confirm: '撤销这条授权？对方将立即失去对应权限。',
              onClick: () => void revoke.run(g),
            },
          ]}
        />
      ),
    },
  ];

  return (
    <AdminPage
      title="资源授权"
      description="把知识库等数据的使用权、技能的编辑与管理权授给成员、部门、用户组或角色；技能、MCP 服务给谁用，在各自详情的「给谁用」里改。空间角色自带的默认权限不在这里列出。"
    >
      <Toolbar
        left={<SearchInput placeholder="搜索对象、资源或权限" value={keyword} onChange={setKeyword} />}
        right={
          <Button color="primary" onClick={() => setAdding(true)}>
            <Plus />
            新增授权
          </Button>
        }
      />
      {grants.isPending ? (
        <Skeleton rows={6} />
      ) : (
        <Table<Grant>
          rowKey={(g) => `${g.subject}|${g.resource}`}
          columns={columns}
          rows={rows}
          loading={grants.isFetching}
          empty={grants.isError ? '授权加载失败，请刷新重试' : keyword ? '没有找到匹配的授权' : '还没有授权'}
        />
      )}
      {adding ? <GrantDialog dir={dir} onClose={() => setAdding(false)} /> : null}
    </AdminPage>
  );
}
