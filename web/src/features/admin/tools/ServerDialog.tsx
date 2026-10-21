/**
 * 添加或修改 MCP 服务。保存时服务端会先连一次、拉工具清单，连不上就不保存（提示原因）。
 * 请求头只能写不能读：修改时只显示名字与末四位，勾选「重新设置」才整组替换。
 */
import { useState } from 'react';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { Checkbox } from '@openai/apps-sdk-ui/components/Checkbox';
import { MinusCircle, Plus } from '@openai/apps-sdk-ui/components/Icon';
import { Input } from '@openai/apps-sdk-ui/components/Input';
import {
  createServer,
  updateServer,
  validateServerInput,
  type HeaderInput,
  type McpServer,
  type ServerInput,
} from '../../../api/mcp';
import { Dialog, DialogActions } from '../../../ui/Dialog';
import { Field } from '../../../ui/Field';
import { rule, useForm, type Rules } from '../../../ui/form';
import { NumberInput } from '../../../ui/NumberInput';
import { toast } from '../../../ui/toastStore';
import { KEYS } from '../queries';
import { useAction } from '../useAction';

interface Values {
  readonly title: string;
  readonly name: string;
  readonly url: string;
  readonly timeoutSeconds: number;
  readonly headers: readonly HeaderInput[];
}

function initialValues(server?: McpServer): Values {
  if (!server)
    return {
      title: '',
      name: '',
      url: '',
      timeoutSeconds: 60,
      headers: [{ name: 'Authorization', value: '' }],
    };
  return {
    title: server.title,
    name: server.name,
    url: server.url,
    timeoutSeconds: server.timeoutSeconds,
    headers: [],
  };
}

function HeaderFields({
  headers,
  onChange,
}: {
  readonly headers: readonly HeaderInput[];
  readonly onChange: (headers: readonly HeaderInput[]) => void;
}) {
  const update = (index: number, patch: Partial<HeaderInput>) =>
    onChange(headers.map((h, i) => (i === index ? { ...h, ...patch } : h)));
  return (
    <div className="kw-header-list">
      {headers.map((header, index) => (
        <div key={index} className="kw-header-row">
          <Input
            placeholder="名字，例如 Authorization"
            aria-label="请求头名字"
            value={header.name}
            onChange={(e) => update(index, { name: e.target.value })}
          />
          <Input
            type="password"
            autoComplete="new-password"
            placeholder="值，例如 Bearer xxxx"
            aria-label="请求头的值"
            value={header.value}
            onChange={(e) => update(index, { value: e.target.value })}
          />
          <Button
            color="secondary"
            variant="ghost"
            uniform
            aria-label="删除这个请求头"
            onClick={() => onChange(headers.filter((_, i) => i !== index))}
          >
            <MinusCircle />
          </Button>
        </div>
      ))}
      <div>
        <Button
          color="secondary"
          variant="outline"
          size="sm"
          onClick={() => onChange([...headers, { name: '', value: '' }])}
        >
          <Plus />
          添加请求头
        </Button>
      </div>
    </div>
  );
}

export function ServerDialog({
  server,
  onClose,
}: {
  readonly server?: McpServer;
  readonly onClose: () => void;
}) {
  const editing = server !== undefined;
  const rules: Rules<Values> = {
    title: rule.required('请输入名称'),
    url: rule.required('请输入地址'),
    ...(editing ? {} : { name: rule.required('请输入标识') }),
  };
  const form = useForm<Values>(initialValues(server), rules);
  const [resetHeaders, setResetHeaders] = useState(!editing);
  const action = useAction(
    (input: ServerInput) => (server ? updateServer(server.id, input) : createServer(input)),
    {
      invalidate: [KEYS.mcpServers],
      success: editing ? '已保存' : '已添加，工具清单已拉取',
      failure: '保存没有成功',
    },
  );
  const submit = async () => {
    const values = form.validate();
    if (!values) return;
    const headers = resetHeaders ? values.headers.filter((h) => h.name.trim() || h.value.trim()) : undefined;
    const input: ServerInput = {
      name: server?.name ?? values.name.trim(),
      title: values.title,
      url: values.url,
      timeoutSeconds: values.timeoutSeconds,
      headers,
    };
    const problem = validateServerInput(input, !editing);
    if (problem) {
      toast.warning(problem);
      return;
    }
    if (await action.run(input)) onClose();
  };
  const { errors } = form;
  return (
    <Dialog
      title={editing ? '修改 MCP 服务' : '添加 MCP 服务'}
      size="md"
      onClose={onClose}
      onSubmit={() => void submit()}
      footer={
        <DialogActions
          okText={action.pending ? '正在连接' : '保存'}
          loading={action.pending}
          onCancel={onClose}
        />
      }
    >
      <Field label="名称" error={errors.title}>
        {(id) => <Input id={id} placeholder="例如：GitHub" maxLength={100} {...form.text('title')} />}
      </Field>
      <Field
        label="标识"
        help={
          editing
            ? '工具名前缀，建好后不能修改'
            : '用作工具名前缀 mcp__标识__，建好后不能修改。小写字母开头，只含小写字母、数字和连字符'
        }
        error={errors.name}
      >
        {(id) => (
          <Input id={id} placeholder="例如 github" maxLength={24} disabled={editing} {...form.text('name')} />
        )}
      </Field>
      <Field label="地址" help="MCP 服务的 Streamable HTTP 端点，一般以 /mcp 结尾" error={errors.url}>
        {(id) => <Input id={id} placeholder="https://mcp.example.com/mcp" {...form.text('url')} />}
      </Field>
      <Field label="请求头" help="鉴权信息放在这里，加密保存，保存后不再显示">
        <>
          {editing ? (
            <div className="kw-header-current">
              <span className="kw-secondary-text">
                {server.headers.length
                  ? server.headers.map((h) => `${h.name}（末四位 ${h.last4 || '****'}）`).join('、')
                  : '没有配置请求头'}
              </span>
              <Checkbox label="重新设置请求头" checked={resetHeaders} onCheckedChange={setResetHeaders} />
            </div>
          ) : null}
          {resetHeaders ? (
            <HeaderFields
              headers={form.values.headers}
              onChange={(headers) => form.set('headers', headers)}
            />
          ) : null}
        </>
      </Field>
      <Field label="单次调用超时（秒）">
        {(id) => (
          <NumberInput
            id={id}
            className="kw-input-narrow"
            min={5}
            max={300}
            value={form.values.timeoutSeconds}
            onChange={(n) => form.set('timeoutSeconds', n ?? 60)}
          />
        )}
      </Field>
    </Dialog>
  );
}
