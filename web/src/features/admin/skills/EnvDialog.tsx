/**
 * 填写技能的环境变量（空间技能、我的技能、企业技能共用）。值只写不读：已填的只显示「已填写」，不改就留空，
 * 也可以勾「清空」。保存后，技能在对话里加载以后跑的命令才带上这些值，命令输出里的值会被换成 ******。
 */
import { useState } from 'react';
import { Checkbox } from '@openai/apps-sdk-ui/components/Checkbox';
import { Input } from '@openai/apps-sdk-ui/components/Input';
import type { SkillEnv } from '../../../api/skills';
import { Dialog, DialogActions } from '../../../ui/Dialog';
import { Field } from '../../../ui/Field';
import { envChanges } from './skillsModel';

const MAX_VALUE = 4096;

interface Props {
  readonly name: string;
  readonly env: readonly SkillEnv[];
  readonly filled: readonly string[];
  readonly pending: boolean;
  /** 只交改了的：名字 → 新值，null 是清掉。返回是否保存成功 */
  readonly onSave: (values: Record<string, string | null>) => Promise<boolean>;
  readonly onClose: () => void;
}

export function EnvDialog({ name, env, filled, pending, onSave, onClose }: Props) {
  const [typed, setTyped] = useState<Record<string, string>>({});
  const [cleared, setCleared] = useState<ReadonlySet<string>>(new Set());
  const toggleClear = (key: string, on: boolean) =>
    setCleared((prev) => {
      const next = new Set(prev);
      if (on) next.add(key);
      else next.delete(key);
      return next;
    });
  const submit = async () => {
    const changes = envChanges(typed, cleared);
    if (Object.keys(changes).length === 0) {
      onClose();
      return;
    }
    if (await onSave(changes)) onClose();
  };
  return (
    <Dialog
      title={`「${name}」的环境变量`}
      onClose={onClose}
      onSubmit={() => void submit()}
      footer={<DialogActions okText="保存" loading={pending} onCancel={onClose} />}
    >
      <p className="kw-secondary-text">
        值保存后看不到原文。技能在对话里加载以后，它跑的命令才带上这些值；命令输出里出现的值会被换成 ******。
      </p>
      {env.map((spec) => {
        const isFilled = filled.includes(spec.name);
        const clearing = cleared.has(spec.name);
        const help = [spec.description, isFilled ? '已填写，不改就留空' : spec.required ? '必填' : '选填']
          .filter(Boolean)
          .join(' · ');
        return (
          <Field key={spec.name} label={spec.name} help={help}>
            {(id) => (
              <div className="kw-check-list">
                <Input
                  id={id}
                  type="password"
                  autoComplete="new-password"
                  maxLength={MAX_VALUE}
                  disabled={clearing}
                  placeholder={isFilled ? '已填写' : '填写值'}
                  value={typed[spec.name] ?? ''}
                  onChange={(e) => setTyped((prev) => ({ ...prev, [spec.name]: e.target.value }))}
                />
                {isFilled ? (
                  <Checkbox
                    label="清空"
                    checked={clearing}
                    onCheckedChange={(checked) => toggleClear(spec.name, checked === true)}
                  />
                ) : null}
              </div>
            )}
          </Field>
        );
      })}
    </Dialog>
  );
}
