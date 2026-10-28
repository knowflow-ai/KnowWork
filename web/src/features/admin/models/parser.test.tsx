import { afterEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent, { pickOption } from '../../../test/user';
import { jsonResponse, ME, renderWithApp, routeFetch } from '../../../test/render';
import { ParserTab } from './ParserTab';

afterEach(() => vi.unstubAllGlobals());

const PATH = '/api/system/document-parser';
const ENV = {
  source: 'env',
  enabled: true,
  base_url: 'http://env-mineru:8000',
  server_url: '',
  api_key_set: false,
  api_key_last4: null,
  lang: 'ch',
  max_concurrency: 3,
  updated_at: null,
};

function api(current = ENV) {
  return routeFetch({
    'GET /api/auth/me': jsonResponse(ME),
    [`GET ${PATH}`]: jsonResponse(current),
    [`PUT ${PATH}`]: (call) =>
      jsonResponse({
        ...current,
        source: 'settings',
        base_url: (call.body as { base_url: string }).base_url,
        api_key_set: true,
        api_key_last4: '1234',
        updated_at: '2026-10-22T10:00:00Z',
      }),
    [`POST ${PATH}/test`]: jsonResponse({ ok: false, message: '服务要求令牌，或令牌不对', latency_ms: 12 }),
  });
}

describe('模型 → 文档解析', () => {
  it('说明现在用的是哪份配置；保存时令牌留空就不改', async () => {
    const calls = api();
    renderWithApp(<ParserTab />);
    expect(
      await screen.findByText(/现在用的是部署时的环境变量（http:\/\/env-mineru:8000）/),
    ).toBeInTheDocument();
    const url = screen.getByLabelText('MinerU 地址');
    await userEvent.clear(url);
    await userEvent.type(url, 'mineru:8000');
    await userEvent.click(screen.getByRole('button', { name: '保存' }));
    expect(await screen.findByText(/要以 http:\/\/ 或 https:\/\/ 开头/)).toBeInTheDocument();
    expect(calls.find('PUT', PATH)).toHaveLength(0);

    await userEvent.clear(url);
    await userEvent.type(url, 'http://mineru:8000');
    await userEvent.type(screen.getByLabelText('VLM 服务地址'), 'http://127.0.0.1:30000');
    await userEvent.click(screen.getByRole('button', { name: '保存' }));
    await waitFor(() => expect(calls.find('PUT', PATH)).toHaveLength(1));
    expect(calls.find('PUT', PATH)[0]?.body).toEqual({
      enabled: true,
      base_url: 'http://mineru:8000',
      server_url: 'http://127.0.0.1:30000',
      backend: 'auto',
      lang: 'ch',
      max_concurrency: 3,
    });
    expect(await screen.findByText('正在使用这里的配置。')).toBeInTheDocument();
    expect(screen.getByText(/末四位 1234/)).toBeInTheDocument();
  });

  it('可以强制 VLM 模式；VLM、hybrid 模式没填 VLM 服务地址时不让保存', async () => {
    const calls = api();
    renderWithApp(<ParserTab />);
    await screen.findByText(/现在用的是部署时的环境变量/);
    await pickOption(document.body, '解析模式', /^VLM/);
    await userEvent.click(screen.getByRole('button', { name: '保存' }));
    expect(await screen.findByText('VLM、hybrid 模式要填 VLM 服务地址')).toBeInTheDocument();
    expect(calls.find('PUT', PATH)).toHaveLength(0);
    await userEvent.type(screen.getByLabelText('VLM 服务地址'), 'http://127.0.0.1:30000');
    await userEvent.click(screen.getByRole('button', { name: '保存' }));
    await waitFor(() => expect(calls.find('PUT', PATH)).toHaveLength(1));
    expect(calls.find('PUT', PATH)[0]?.body).toMatchObject({
      backend: 'vlm-http-client',
      server_url: 'http://127.0.0.1:30000',
    });
  });

  it('测试连接把原因显示出来；新令牌一起带上', async () => {
    const calls = api({ ...ENV, source: 'none', base_url: '' });
    renderWithApp(<ParserTab />);
    expect(await screen.findByText(/还没有配置/)).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText('MinerU 地址'), 'http://mineru:8000');
    await userEvent.type(screen.getByLabelText('访问令牌'), 'token-123456');
    await userEvent.click(screen.getByRole('button', { name: '测试连接' }));
    expect(await screen.findByText('服务要求令牌，或令牌不对')).toBeInTheDocument();
    expect(calls.find('POST', `${PATH}/test`)[0]?.body).toEqual({
      base_url: 'http://mineru:8000',
      api_key: 'token-123456',
    });
  });
});
