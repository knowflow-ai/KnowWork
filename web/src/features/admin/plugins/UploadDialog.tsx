/**
 * 上传插件的对话框（管理员传空间插件、成员传个人插件共用）：选一个 .tgz、勾「适用于」。
 * 上传时只做静态检查，不执行包里的代码；检查不过的当场说明原因。
 * 勾了「本机对话」时提示：插件在员工电脑上运行，能读写本机文件、执行命令、联网，没有容器隔离。
 */
import { useRef, useState } from 'react';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { Checkbox } from '@openai/apps-sdk-ui/components/Checkbox';
import { FileUpload } from '@openai/apps-sdk-ui/components/Icon';
import { readableError } from '../../../api/http';
import { PLUGIN_ARCHIVE_MAX_BYTES, PLUGIN_TARGETS, type PluginTarget } from '../../../api/plugins';
import { Dialog, DialogActions } from '../../../ui/Dialog';
import { Field } from '../../../ui/Field';

const TARGET_HELP: Readonly<Record<PluginTarget, string>> = {
  cloud: '在服务器上每个人自己的隔离环境里运行',
  device: '装到桌面端，在员工自己的电脑上运行',
};

export function UploadDialog({
  title,
  targets: offered = ['cloud', 'device'],
  onUpload,
  onClose,
}: {
  readonly title: string;
  /** 这次能选哪些「适用于」（空间不许给本机对话装个人插件时只剩云端） */
  readonly targets?: readonly PluginTarget[];
  readonly onUpload: (archive: File, targets: readonly PluginTarget[]) => Promise<void>;
  readonly onClose: () => void;
}) {
  const picker = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File>();
  const [targets, setTargets] = useState<readonly PluginTarget[]>(() => [...offered]);
  const [problem, setProblem] = useState<string>();
  const [pending, setPending] = useState(false);
  const submit = async () => {
    if (!file) return setProblem('请先选一个 .tgz 压缩包');
    if (file.size > PLUGIN_ARCHIVE_MAX_BYTES) return setProblem('压缩包超过 100MB 上限');
    if (targets.length === 0) return setProblem('「适用于」至少选一个');
    setProblem(undefined);
    setPending(true);
    try {
      await onUpload(file, targets);
      onClose();
    } catch (error) {
      setProblem(readableError(error, '没能上传，请稍后重试'));
    } finally {
      setPending(false);
    }
  };
  const toggle = (target: PluginTarget, on: boolean) =>
    setTargets(PLUGIN_TARGETS.map((t) => t.value).filter((t) => (t === target ? on : targets.includes(t))));
  return (
    <Dialog
      title={title}
      description="插件是 DSH 的插件包（npm pack 打出的 .tgz，依赖要打在包里）。上传时只检查、不运行。"
      onClose={onClose}
      onSubmit={() => void submit()}
      footer={<DialogActions okText="上传" loading={pending} onCancel={onClose} />}
    >
      <div className="kw-field-stack">
        <Field label="压缩包" help="不超过 100MB">
          <div className="kw-plugin-upload-file">
            <Button color="secondary" variant="outline" size="sm" onClick={() => picker.current?.click()}>
              <FileUpload />
              选择文件
            </Button>
            <span className="kw-secondary-text">{file ? file.name : '还没有选'}</span>
            <input
              ref={picker}
              type="file"
              hidden
              accept=".tgz,.tar.gz,application/gzip,application/x-gzip"
              aria-label="选择插件压缩包"
              onChange={(event) => {
                setFile(event.target.files?.[0]);
                setProblem(undefined);
                event.target.value = '';
              }}
            />
          </div>
        </Field>
        <Field label="适用于">
          <div className="kw-plugin-upload-targets">
            {PLUGIN_TARGETS.filter((target) => offered.includes(target.value)).map((target) => (
              <Checkbox
                key={target.value}
                checked={targets.includes(target.value)}
                onCheckedChange={(on) => toggle(target.value, on)}
                label={
                  <span className="kw-plugin-upload-target">
                    <span>{target.label}</span>
                    <span className="kw-secondary">{TARGET_HELP[target.value]}</span>
                  </span>
                }
              />
            ))}
          </div>
        </Field>
        {targets.includes('device') ? (
          <p className="kw-plugin-upload-warning">
            适用于本机对话的插件在员工自己的电脑上运行，能读写本机文件、执行命令、联网，没有容器隔离。只上传来源可信的插件。
          </p>
        ) : null}
        {problem ? (
          <p className="kw-plugin-upload-problem" role="alert">
            {problem}
          </p>
        ) : null}
      </div>
    </Dialog>
  );
}
