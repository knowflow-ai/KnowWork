/**
 * 账号页里的「已登录的设备」：在桌面端等应用里登录过的设备（OAuth 授权，后端 /api/auth/devices），可以让某台退出登录。
 * 一台都没有时整栏不显示：没装这类应用的部署（开源版没有登记任何应用）不多出一块空表。
 */
import { useQuery } from '@tanstack/react-query';
import { listDevices, signOutDevice, type DeviceLogin } from '../../api/oauth';
import { formatDateTime } from '../../utils/format';
import { RowActions } from '../admin/common';
import { useAction } from '../admin/useAction';
import { Table, type Column } from '../../ui/Table';

const DEVICES = ['me', 'devices'] as const;

export function DevicesSection() {
  const devices = useQuery({ queryKey: DEVICES, queryFn: listDevices });
  const signOut = useAction((id: string) => signOutDevice(id), {
    invalidate: [DEVICES],
    success: '已让这台设备退出登录',
    failure: '退出登录没有成功，请稍后重试',
  });
  const rows = devices.data ?? [];
  if (rows.length === 0) return null;
  const columns: readonly Column<DeviceLogin>[] = [
    { key: 'client', title: '应用', width: 160, ellipsis: true, render: (d) => d.clientName },
    { key: 'device', title: '设备', ellipsis: true, render: (d) => d.deviceName || '—' },
    { key: 'created', title: '登录时间', width: 150, render: (d) => formatDateTime(d.createdAt) },
    { key: 'lastUsed', title: '最近使用', width: 150, render: (d) => formatDateTime(d.lastUsedAt) },
    {
      key: 'actions',
      title: '操作',
      width: 100,
      render: (d) => (
        <RowActions
          actions={[
            {
              key: 'sign-out',
              label: '退出登录',
              danger: true,
              confirm: `让「${d.deviceName || d.clientName}」退出登录？这台设备上的应用要重新登录才能用。`,
              onClick: () => void signOut.run(d.id),
            },
          ]}
        />
      ),
    },
  ];
  return (
    <section className="kw-settings-devices" aria-label="已登录的设备">
      <h2>已登录的设备</h2>
      <p className="kw-secondary">用你的账号登录过的应用和设备。不认识的，请让它退出登录。</p>
      <Table<DeviceLogin> rowKey={(d) => d.id} columns={columns} rows={rows} minWidth={720} />
    </section>
  );
}
