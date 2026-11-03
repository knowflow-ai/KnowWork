/**
 * 模型 → 文档解析：配置 MinerU 4.1+（会话附件与扩展解析 PDF、Word、PPT 用）。保存后几秒内生效，不用重启；
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
  PARSER_TIERS,
  saveDocumentParser,
  testDocumentParser,
  validateParserUrl,
  type DocumentParser,
  type ParserProbe,
  type ParserTier,
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
  const [tier, setTier] = useState<ParserTier>(parser.tier);
  const [apiKey, setApiKey] = useState('');
  const [clearKey, setClearKey] = useState(false);
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
    setProblem(found);
    return !found;
  };
  const submit = () => {
    if (!check()) return;
    save.mutate({
      enabled,
      baseUrl: baseUrl.trim() || parser.baseUrl,
      tier,
      apiKey: clearKey ? '' : apiKey.trim() || undefined,
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
          help="MinerU 4.1+ 的 API 地址，业务服务与 worker 都要能访问到"
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
          label="解析档位"
          help="决定解析质量与速度；自动时 MinerU 按文档推荐。VLM 推理服务、识别语言由 MinerU 侧部署配置，这里不用填"
        >
          <Select
            block
            options={PARSER_TIERS.map((t) => ({ ...t }))}
            value={tier}
            onChange={(option) => setTier(option.value as ParserTier)}
          />
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
