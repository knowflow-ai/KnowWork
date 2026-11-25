/**
 * 运营 → 内容安全：管理员给空间配敏感词。员工发给模型的内容、模型的回答和它要调用的工具参数，命中就拦下、给员工一句
 * 阻断提示，并记进审计（动作 content_safety.blocked，不记原文）。每个空间一份，管理员在哪个空间就配哪个空间的。
 * 两个检查方向不是即时生效的开关，和词表、提示一起一次保存（所以用勾选加保存按钮，不用 Switch）。
 * 词表：管理员自己填的自定义词表（文本框，也可以上传 txt 读进来再改）；扩展登记了现成词表（例如内置的常见违规词）时，
 * 上面多一组勾选，每份可以下载查看，勾上的和自定义词合起来查。
 */
import { useRef, useState, type ChangeEvent, type ReactNode } from 'react';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { Checkbox } from '@openai/apps-sdk-ui/components/Checkbox';
import { FileUpload, Lock, Warning } from '@openai/apps-sdk-ui/components/Icon';
import { Textarea } from '@openai/apps-sdk-ui/components/Textarea';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  CUSTOM_SOURCE,
  getContentSafety,
  MAX_MESSAGE_CHARS,
  readWordsFile,
  saveContentSafety,
  sourceWordsUrl,
  splitWords,
  validateContentSafety,
  type ContentSafetyInput,
  type ContentSafetyPolicy,
  type WordSource,
} from '../../../api/contentSafety';
import { ApiError, readableError } from '../../../api/http';
import { currentWorkspaceId } from '../../../stores/workspace';
import { Empty } from '../../../ui/Empty';
import { Field } from '../../../ui/Field';
import { Skeleton } from '../../../ui/Skeleton';
import { toast } from '../../../ui/toastStore';
import { AdminPage } from '../AdminPage';

const keyOf = (workspaceId: string) => ['admin', 'content-safety', workspaceId] as const;

function Choice({
  title,
  description,
  checked,
  onChange,
  extra,
}: {
  readonly title: string;
  readonly description: string;
  readonly checked: boolean;
  readonly onChange: (next: boolean) => void;
  readonly extra?: ReactNode;
}) {
  return (
    <div className="kw-content-safety-choice">
      <Checkbox
        checked={checked}
        onCheckedChange={onChange}
        label={
          <span className="kw-content-safety-direction">
            <span>{title}</span>
            <span className="kw-secondary">{description}</span>
          </span>
        }
      />
      {extra}
    </div>
  );
}

function SourceChoice({
  source,
  checked,
  onChange,
}: {
  readonly source: WordSource;
  readonly checked: boolean;
  readonly onChange: (next: boolean) => void;
}) {
  return (
    <Choice
      title={source.label}
      description={source.description}
      checked={checked}
      onChange={onChange}
      extra={
        <a className="kw-link kw-content-safety-download" href={sourceWordsUrl(source.id)} download>
          下载查看
        </a>
      }
    />
  );
}

/** 自定义词表：文本框，右上角「上传 txt」把文件里的词读进来（替换文本框里的，可以再改） */
function CustomWords({
  value,
  onChange,
}: {
  readonly value: string;
  readonly onChange: (next: string) => void;
}) {
  const picker = useRef<HTMLInputElement>(null);
  const count = splitWords(value).length;
  const pick = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    try {
      const words = splitWords(await readWordsFile(file));
      onChange(words.join('\n'));
      toast.success(`已读入 ${words.length} 个词，检查后点保存`);
    } catch (error) {
      toast.error(readableError(error, '这个文件读不了，请换一个 txt 文件'));
    }
  };
  return (
    <Field
      label="自定义词表"
      help={
        <span className="kw-content-safety-help">
          <span>每行一个词，也可以用逗号、分号隔开；按字面匹配，区分大小写；最多 3 万个词</span>
          <span>已填 {count} 个</span>
        </span>
      }
    >
      {(id) => (
        <div className="kw-content-safety-words">
          <div className="kw-content-safety-words-bar">
            <Button color="secondary" variant="ghost" size="sm" onClick={() => picker.current?.click()}>
              <FileUpload />
              上传 txt
            </Button>
            <input
              ref={picker}
              type="file"
              hidden
              accept=".txt,text/plain"
              aria-label="选择词表 txt 文件"
              onChange={(event) => void pick(event)}
            />
          </div>
          <Textarea
            id={id}
            rows={10}
            placeholder={'星河计划\n某某银行'}
            value={value}
            onChange={(event) => onChange(event.target.value)}
          />
        </div>
      )}
    </Field>
  );
}

function PolicyForm({ policy }: { readonly policy: ContentSafetyPolicy }) {
  const client = useQueryClient();
  const key = keyOf(currentWorkspaceId());
  // 打开时的版本：保存时带上；别人先改过时换成最新的版本，本页的修改留着，再点保存就覆盖
  const [version, setVersion] = useState(policy.version);
  const [input, setInput] = useState(policy.version === 0 ? true : policy.inputEnabled);
  const [output, setOutput] = useState(policy.version === 0 ? true : policy.outputEnabled);
  const [sources, setSources] = useState<readonly string[]>(policy.wordSources);
  const [words, setWords] = useState(policy.words);
  const [message, setMessage] = useState(policy.blockMessage);
  const [problem, setProblem] = useState<string>();
  const offered = policy.availableSources;
  // 没有现成词表（开源版）时不列勾选，自定义词表就是唯一的一种
  const custom = offered.length === 0 || sources.includes(CUSTOM_SOURCE);
  const toggle = (id: string, on: boolean) =>
    setSources((current) =>
      on ? [...current.filter((item) => item !== id), id] : current.filter((item) => item !== id),
    );
  const save = useMutation({
    mutationFn: (draft: ContentSafetyInput) => saveContentSafety(draft),
    onSuccess: (saved) => {
      client.setQueryData(key, saved);
      setVersion(saved.version);
      setWords(saved.words);
      toast.success('已保存，5 秒内生效');
    },
    onError: async (error) => {
      if (error instanceof ApiError && error.status === 409) {
        const latest = await client.fetchQuery({ queryKey: key, queryFn: getContentSafety, staleTime: 0 });
        setVersion(latest.version);
        toast.warning('配置已被其他管理员修改，已载入最新内容；再点保存会用本页的内容覆盖');
        return;
      }
      toast.error(readableError(error, '配置没能保存，请稍后重试'));
    },
  });
  const submit = () => {
    const wordSources = offered.length === 0 ? [CUSTOM_SOURCE] : sources;
    const draft = {
      version,
      inputEnabled: input,
      outputEnabled: output,
      wordSources,
      words,
      blockMessage: message,
    };
    const found = validateContentSafety(draft);
    setProblem(found);
    if (!found) save.mutate(draft);
  };
  return (
    <div className="kw-content-safety">
      <div className="kw-field-stack">
        <Field label="检查范围" help="两项都不勾就是关闭">
          <div className="kw-content-safety-directions">
            <Choice
              title="检查输入"
              description="员工的消息，以及附件、知识库、工具取回的内容，发给模型之前检查"
              checked={input}
              onChange={setInput}
            />
            <Choice
              title="检查输出"
              description="模型的回答和它要调用的工具参数，显示和执行之前检查"
              checked={output}
              onChange={setOutput}
            />
          </div>
        </Field>
        {offered.length > 0 ? (
          <Field label="词表" help="勾上的几种合在一起查">
            <div className="kw-content-safety-directions">
              {offered.map((source) => (
                <SourceChoice
                  key={source.id}
                  source={source}
                  checked={sources.includes(source.id)}
                  onChange={(on) => toggle(source.id, on)}
                />
              ))}
              <Choice
                title="自定义词表"
                description="自己填的词，例如项目代号、客户名"
                checked={sources.includes(CUSTOM_SOURCE)}
                onChange={(on) => toggle(CUSTOM_SOURCE, on)}
              />
            </div>
          </Field>
        ) : null}
        {custom ? <CustomWords value={words} onChange={setWords} /> : null}
        <Field label="阻断提示" help={`命中时显示给员工，不超过 ${MAX_MESSAGE_CHARS} 字`}>
          {(id) => (
            <Textarea
              id={id}
              rows={2}
              maxLength={MAX_MESSAGE_CHARS}
              value={message}
              onChange={(event) => setMessage(event.target.value)}
            />
          )}
        </Field>
      </div>
      {problem ? (
        <p className="kw-content-safety-problem" role="alert">
          {problem}
        </p>
      ) : null}
      <div className="kw-form-actions">
        <Button color="primary" loading={save.isPending} onClick={submit}>
          保存
        </Button>
      </div>
    </div>
  );
}

export default function ContentSafetyPage() {
  const policy = useQuery({ queryKey: keyOf(currentWorkspaceId()), queryFn: getContentSafety });
  let body;
  if (policy.isPending) body = <Skeleton rows={6} />;
  else if (policy.isError) {
    body =
      policy.error instanceof ApiError && policy.error.status === 403 ? (
        <Empty area icon={<Lock />} title="没有权限" description="只有空间所有者和管理员能配置内容安全" />
      ) : (
        <Empty
          area
          icon={<Warning />}
          title="没能加载配置"
          description={readableError(policy.error, '请稍后刷新重试')}
        />
      );
  } else body = <PolicyForm policy={policy.data} />;
  return (
    <AdminPage
      title="内容安全"
      description="员工发给模型的内容和模型的回答，命中企业敏感词就拦下，并记进审计。"
    >
      {body}
    </AdminPage>
  );
}
