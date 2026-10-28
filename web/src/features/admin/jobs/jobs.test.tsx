import { afterEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent, { pickOption } from '../../../test/user';
import { jsonResponse, ME, renderWithApp, routeFetch } from '../../../test/render';
import { waited } from './labels';
import JobsPage from './JobsPage';

afterEach(() => vi.unstubAllGlobals());

const OVERVIEW = {
  pools: [
    {
      pool: 'core',
      slots: 2,
      queued: 3,
      retrying: 1,
      running: 1,
      failed: 2,
      oldest_wait_seconds: 125,
      succeeded_recent: 40,
      failed_recent: 2,
    },
  ],
  workers: [
    {
      id: 'w1',
      host: 'worker-a',
      pid: 12,
      version: '0.1.0',
      pools: { core: 2, maintenance: 1 },
      busy: { core: 1 },
      alive: true,
      started_at: '2026-10-22T01:00:00Z',
      heartbeat_at: '2026-10-22T02:00:00Z',
    },
    {
      id: 'w0',
      host: 'worker-old',
      pid: 9,
      version: '0.0.9',
      pools: {},
      busy: {},
      alive: false,
      started_at: '2026-10-21T01:00:00Z',
      heartbeat_at: '2026-10-21T02:00:00Z',
    },
  ],
  gates: [{ name: 'mineru', holding: 1, limit: 3, shared: true }],
  kinds: [
    { name: 'kb.parse', label: '知识库文档解析', pool: 'core', cancelable: true },
    { name: 'kb.export', label: '导出知识库', pool: 'maintenance', cancelable: false },
  ],
};

function job(id: string, extra: Record<string, unknown>) {
  return {
    id,
    kind: 'kb.parse',
    subject_id: `doc_${id}`,
    pool: 'core',
    status: 'queued',
    stage: null,
    tries: 0,
    max_tries: 6,
    error_code: null,
    error_class: null,
    cancel_requested: false,
    created_at: '2026-10-22T01:00:00Z',
    updated_at: '2026-10-22T01:00:00Z',
    next_attempt_at: '2026-10-22T01:00:00Z',
    finished_at: null,
    ...extra,
  };
}

const JOBS = [
  job('j_failed', { status: 'failed', tries: 6, error_code: 'mineru_unavailable', error_class: 'service' }),
  job('j_retry', { tries: 2, error_code: 'busy' }),
  job('j_export', {
    kind: 'kb.export',
    pool: 'maintenance',
    status: 'running',
    stage: 'documents',
    tries: 1,
  }),
];

function api(allowed = true, extra = {}) {
  return routeFetch({
    'GET /api/auth/me': jsonResponse(ME),
    'GET /api/system/check': jsonResponse({ allowed }),
    'GET /api/workspaces/default/jobs/overview': jsonResponse(OVERVIEW),
    'GET /api/workspaces/default/jobs': jsonResponse({ items: JOBS, next: null }),
    'GET /api/workspaces/default/jobs/*': jsonResponse({
      ...JOBS[0],
      stages: [
        {
          try_no: 6,
          name: 'parse',
          status: 'failed',
          error_code: 'mineru_unavailable',
          detail: { bytes: 2048 },
          started_at: '2026-10-22T01:00:00Z',
          finished_at: '2026-10-22T01:00:03Z',
        },
      ],
    }),
    'POST /api/workspaces/default/jobs/*/retry': jsonResponse(job('j_failed', {})),
    'POST /api/workspaces/default/jobs/*/cancel': jsonResponse(job('j_retry', { cancel_requested: true })),
    'DELETE /api/workspaces/default/jobs/*': new Response(null, { status: 204 }),
    ...extra,
  });
}

const rowOf = async (text: string) => {
  const table = await screen.findByRole('table', { name: '任务' });
  const cell = await within(table).findByText(text);
  const row = cell.closest('tr');
  if (!row) throw new Error(`找不到 ${text} 所在的行`);
  return row;
};

describe('运行面板', () => {
  it('任务池、下游并发与 worker 心跳', async () => {
    api();
    renderWithApp(<JobsPage />);
    const pools = await screen.findByRole('region', { name: '任务池' });
    expect(within(pools).getByText('解析（core）')).toBeInTheDocument();
    expect(within(pools).getByText('2 分钟')).toBeInTheDocument();
    expect(within(pools).getByText('40 / 2')).toBeInTheDocument();
    const gates = screen.getByRole('region', { name: '下游并发' });
    expect(within(gates).getByText('MinerU 文档解析')).toBeInTheDocument();
    expect(within(gates).getByText('1 / 3')).toBeInTheDocument();
    expect(within(gates).getByText('所有 worker 共用这些名额')).toBeInTheDocument();
    const workers = screen.getByRole('region', { name: 'Worker' });
    expect(within(workers).getByText('解析 2 · 维护 1')).toBeInTheDocument();
    expect(within(workers).getByText('在线')).toBeInTheDocument();
    expect(within(workers).getByText('失联')).toBeInTheDocument();
  });

  it('任务按中文名显示；能做的操作跟着状态走，运行中的导出不能取消', async () => {
    api();
    renderWithApp(<JobsPage />);
    const failed = await rowOf('doc_j_failed');
    expect(within(failed).getByText('知识库文档解析')).toBeInTheDocument();
    expect(within(failed).getByRole('button', { name: '立即重跑' })).toBeInTheDocument();
    expect(within(failed).getByRole('button', { name: '删除记录' })).toBeInTheDocument();
    expect(within(failed).queryByRole('button', { name: '取消任务' })).not.toBeInTheDocument();

    const retrying = await rowOf('doc_j_retry');
    expect(within(retrying).getByText('等待重试')).toBeInTheDocument();
    expect(within(retrying).getByRole('button', { name: '取消任务' })).toBeInTheDocument();

    const exporting = await rowOf('doc_j_export');
    expect(within(exporting).getByText('导出知识库')).toBeInTheDocument();
    expect(within(exporting).queryByRole('button', { name: '取消任务' })).not.toBeInTheDocument();
  });

  it('立即重跑直接发；取消要先确认', async () => {
    const calls = api();
    renderWithApp(<JobsPage />);
    await userEvent.click(within(await rowOf('doc_j_failed')).getByRole('button', { name: '立即重跑' }));
    await waitFor(() =>
      expect(calls.find('POST', '/api/workspaces/default/jobs/j_failed/retry')).toHaveLength(1),
    );
    expect(await screen.findByText('已重新排队')).toBeInTheDocument();

    await userEvent.click(within(await rowOf('doc_j_retry')).getByRole('button', { name: '取消任务' }));
    const pop = await screen.findByRole('alertdialog', { name: /按处理失败收尾/ });
    expect(calls.find('POST', '/api/workspaces/default/jobs/j_retry/cancel')).toHaveLength(0);
    await userEvent.click(within(pop).getByRole('button', { name: '取消任务' }));
    await waitFor(() =>
      expect(calls.find('POST', '/api/workspaces/default/jobs/j_retry/cancel')).toHaveLength(1),
    );
  });

  it('按状态筛选；详情里看各阶段，不显示错误详情', async () => {
    const calls = api();
    renderWithApp(<JobsPage />);
    const tasks = await screen.findByRole('region', { name: '任务' });
    await pickOption(tasks, '状态', '失败');
    await waitFor(() =>
      expect(
        calls.calls.some(
          (c) => c.path === '/api/workspaces/default/jobs' && c.query.get('status') === 'failed',
        ),
      ).toBe(true),
    );
    await userEvent.click(within(await rowOf('doc_j_failed')).getByRole('button', { name: '详情' }));
    const stages = await screen.findByRole('table', { name: '各阶段' });
    expect(within(stages).getByText('parse')).toBeInTheDocument();
    expect(within(stages).getByText('bytes: 2048')).toBeInTheDocument();
    expect(screen.getByText(/mineru_unavailable（服务问题）/)).toBeInTheDocument();
  });

  it('不是系统管理员时看到没有权限，不去取运行情况', async () => {
    const calls = api(false);
    renderWithApp(<JobsPage />);
    expect(await screen.findByText('没有权限')).toBeInTheDocument();
    expect(calls.find('GET', '/api/workspaces/default/jobs/overview')).toHaveLength(0);
  });

  it('等待时长按秒、分钟、小时写', () => {
    expect(waited(null)).toBe('—');
    expect(waited(42)).toBe('42 秒');
    expect(waited(600)).toBe('10 分钟');
    expect(waited(7200)).toBe('2 小时');
    expect(waited(7500)).toBe('2 小时 5 分');
  });
});
