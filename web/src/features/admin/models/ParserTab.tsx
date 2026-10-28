/**
 * 模型 → 文档解析：配置 MinerU（会话附件与扩展解析 PDF、Word、PPT 用）。保存后几秒内生效，不用重启；
 * 没在这里配时用部署时的环境变量。令牌只在 MinerU 前面有网关要求时才填，保存后只显示末四位。
 */
import { Warning } from '@openai/apps-sdk-ui/components/Icon';
import { useState } from 'react';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { Input } from '@openai/apps-sdk-ui/components/Input';
import { Select } from '@openai/apps-sdk-ui/components/Select';
import { Switch } from '@openai/apps-sdk-ui/components/Switch';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { readableError } from '../../../api/http';
import {
  getDocumentParser,
  PARSER_BACKENDS,
  PARSER_LANGS,
  saveDocumentParser,
  testDocumentParser,
  validateParserUrl,
  validateServerUrl,
  type DocumentParser,
  type ParserBackend,
  type ParserProbe,
} from '../../../api/documentParser';
import { Empty } from '../../../ui/Empty';
import { Field } from '../../../ui/Field';
import { NumberInput } from '../../../ui/NumberInput';
import { Skeleton } from '../../../ui/Skeleton';
import { StatusDot } from '../../../ui/StatusDot';
import { toast } from '../../../ui/toastStore';

const KEY = ['admin', 'document-parser'] as const;

function sourceNote(parser: DocumentParser): string {
  if (parser.source === 'settings') {
    return parser.enabled ? '正在使用这里的配置。' : '这里停用了文档解析：PDF、Word、PPT 暂时不解析。';
  }
  if (parser.source === 'env') {
    return `现在用的是部署时的环境变量（${parser.baseUrl}）；在这里保存后以这里为准。`;
  }
  return '还没有配置：对话附件里的 PDF、Word、PPT 不会另外解析出 Markdown，知识库里这几种文档解析不了。';
}

function ParserForm({ parser }: { readonly parser: DocumentParser }) {
  const client = useQueryClient();
  const [enabled, setEnabled] = useState(parser.source === 'none' ? true : parser.enabled);
  const [baseUrl, setBaseUrl] = useState(parser.baseUrl);
  const [serverUrl, setServerUrl] = useState(parser.serverUrl);
  const [backend, setBackend] = useState<ParserBackend>(parser.backend);
  const [serverProblem, setServerProblem] = useState<string>();
  const [apiKey, setApiKey] = useState('');
  const [clearKey, setClearKey] = useState(false);
  const [lang, setLang] = useState(parser.lang);
  const [concurrency, setConcurrency] = useState<number | null>(parser.maxConcurrency);
  const [problem, setProblem] = useState<string>();
  const [probe, setProbe] = useState<ParserProbe>();
  const save = useMutation({
    mutationFn: saveDocumentParser,
    onSuccess: (saved) => {
      client.setQueryData(KEY, saved);
      toast.success('文档解析配置已保存，几秒内生效');
    },
    onError: (error) => toast.error(readableError(error, '配置没有保存成功')),
  });
  const test = useMutation({
    mutationFn: () => testDocumentParser(baseUrl, apiKey.trim() || undefined),
    onSuccess: setProbe,
    onError: (error) => setProbe({ ok: false, message: readableError(error, '没有测成'), latencyMs: 0 }),
  });
  const check = () => {
    const found = enabled ? validateParserUrl(baseUrl) : undefined;
    const vlm = validateServerUrl(serverUrl, enabled ? backend : 'auto');
    setProblem(found);
    setServerProblem(vlm);
    return !found && !vlm;
  };
  const submit = () => {
    if (!check()) return;
    save.mutate({
      enabled,
      baseUrl: baseUrl.trim() || parser.baseUrl,
      serverUrl: serverUrl.trim(),
      backend,
      apiKey: clearKey ? '' : apiKey.trim() || undefined,
      lang,
      maxConcurrency: concurrency ?? 3,
    });
  };
  const keyHelp = parser.apiKeySet
    ? `已设置令牌${parser.apiKeyLast4 ? `（末四位 ${parser.apiKeyLast4}）` : ''}，不改就留空`
    : 'MinerU 前面有网关要求令牌时才填，一般留空';
  return (
    <div className="kw-system-section">
      <p className="kw-secondary">{sourceNote(parser)}</p>
      <div className="kw-field-stack">
        <Switch label="启用文档解析" checked={enabled} onCheckedChange={setEnabled} />
        <Field
          label="MinerU 地址"
          error={problem}
          help="MinerU 3.x 的 API 地址，业务服务与 worker 都要能访问到"
        >
          {(id) => (
            <Input
              id={id}
              placeholder="http://mineru:8000"
              value={baseUrl}
              invalid={Boolean(problem)}
              onChange={(event) => setBaseUrl(event.target.value)}
            />
          )}
        </Field>
        <Field
          label="解析模式"
          help="会话附件与知识库都按这里的模式解析；自动时各按用途选。hybrid 自动判断版面效果不好时，可以改用 VLM"
        >
          <Select
            block
            options={PARSER_BACKENDS.map((b) => ({ ...b }))}
            value={backend}
            onChange={(option) => {
              setBackend(option.value as ParserBackend);
              setServerProblem(undefined);
            }}
          />
        </Field>
        <Field
          label="VLM 服务地址"
          error={serverProblem}
          help="MinerU 的 VLM 推理服务（sglang 或 vllm，常见 30000 端口）。这是 MinerU 去访问的地址，和 MinerU 装在同一台机器时填 http://127.0.0.1:30000。VLM、hybrid 模式必填；自动模式下不填就用 pipeline"
        >
          {(id) => (
            <Input
              id={id}
              placeholder="http://127.0.0.1:30000"
              value={serverUrl}
              invalid={Boolean(serverProblem)}
              onChange={(event) => setServerUrl(event.target.value)}
            />
          )}
        </Field>
        <Field label="访问令牌" help={keyHelp}>
          {(id) => (
            <Input
              id={id}
              type="password"
              autoComplete="off"
              value={apiKey}
              disabled={clearKey}
              onChange={(event) => setApiKey(event.target.value)}
            />
          )}
        </Field>
        {parser.apiKeySet ? (
          <Switch label="清除已保存的令牌" checked={clearKey} onCheckedChange={setClearKey} />
        ) : null}
        <Field label="识别语言" help="扫描件、图片里文字的语言">
          <Select
            block
            options={[...PARSER_LANGS]}
            value={lang}
            onChange={(option) => setLang(option.value)}
          />
        </Field>
        <Field
          label="同时解析上限"
          help="按 MinerU 的 max_concurrent_requests 设；附件与知识库、所有 worker 一起算"
        >
          {(id) => <NumberInput id={id} min={1} max={64} value={concurrency} onChange={setConcurrency} />}
        </Field>
      </div>
      {probe ? (
        <p className="kw-parser-probe">
          <StatusDot tone={probe.ok ? 'success' : 'error'}>{probe.message}</StatusDot>
          {probe.ok ? <span className="kw-secondary"> {probe.latencyMs} 毫秒</span> : null}
        </p>
      ) : null}
      <div className="kw-form-actions">
        <Button
          color="secondary"
          variant="outline"
          loading={test.isPending}
          onClick={() => {
            if (!validateParserUrl(baseUrl) || check()) test.mutate();
          }}
        >
          测试连接
        </Button>
        <Button color="primary" loading={save.isPending} onClick={submit}>
          保存
        </Button>
      </div>
    </div>
  );
}

export function ParserTab() {
  const parser = useQuery({ queryKey: KEY, queryFn: getDocumentParser });
  if (parser.isPending) return <Skeleton rows={5} />;
  if (parser.isError)
    return <Empty area icon={<Warning />} title="配置加载失败" description="请稍后刷新重试" />;
  // 保存后取回的值变了，换 key 让表单按新值重来
  return <ParserForm key={`${parser.data.source}-${parser.data.updatedAt ?? ''}`} parser={parser.data} />;
}
