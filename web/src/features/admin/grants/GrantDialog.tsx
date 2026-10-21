/** 新增授权：选授权对象、资源与权限档位。同一对象对同一资源只保留一档，再授即改档。 */
import { useState } from 'react';
import { Checkbox } from '@openai/apps-sdk-ui/components/Checkbox';
import { Input } from '@openai/apps-sdk-ui/components/Input';
import { Select } from '@openai/apps-sdk-ui/components/Select';
import {
  ACTIONS,
  grant,
  RESOURCE_TYPES,
  SUBJECT_KINDS,
  type Action,
  type SubjectKind,
} from '../../../api/workspace';
import { Dialog, DialogActions } from '../../../ui/Dialog';
import { Field } from '../../../ui/Field';
import { useAction } from '../useAction';
import { KEYS } from '../queries';
import type { Directory } from './labels';
import { resourceOptions, subjectOptions, validateGrant, type GrantInput } from './form';
import { KnowledgeBasePicker } from './KnowledgeBasePicker';

/** 校验不通过时，错误挂在对应的表单项下面。 */
function problemField(problem: string | null): 'subject' | 'resource' | null {
  if (!problem) return null;
  return problem.includes('对象') ? 'subject' : 'resource';
}

export function GrantDialog({ dir, onClose }: { readonly dir: Directory; readonly onClose: () => void }) {
  const [value, setValue] = useState<GrantInput>({
    subjectKind: 'user',
    resourceType: 'knowledge_base',
    resourceId: '',
    all: false,
    action: 'use',
  });
  const [problem, setProblem] = useState<string | null>(null);
  const patch = (next: Partial<GrantInput>) => setValue((v) => ({ ...v, ...next }));
  const action = useAction(
    (input: GrantInput) =>
      grant(
        `${input.subjectKind}:${input.subjectId}`,
        `${input.resourceType}:${input.all ? '*' : input.resourceId.trim()}`,
        input.action,
      ),
    { invalidate: [KEYS.grants], success: '已授权', failure: '授权没有成功，请稍后重试' },
  );
  const submit = async () => {
    const found = validateGrant(value);
    setProblem(found);
    if (found) return;
    if (await action.run(value)) onClose();
  };
  const known = resourceOptions(value.resourceType, dir);
  const at = problemField(problem);
  return (
    <Dialog
      title="新增授权"
      onClose={onClose}
      onSubmit={() => void submit()}
      footer={<DialogActions okText="授权" loading={action.pending} onCancel={onClose} />}
    >
      <div className="kw-field-row kw-field-row-narrow">
        <Field label="对象类型">
          <Select
            block
            value={value.subjectKind}
            options={SUBJECT_KINDS.map((k) => ({ ...k }))}
            onChange={(option) => patch({ subjectKind: option.value as SubjectKind, subjectId: undefined })}
          />
        </Field>
        <Field label="授权对象" error={at === 'subject' ? problem : undefined}>
          <Select
            block
            value={value.subjectId ?? ''}
            placeholder="请选择"
            searchPlaceholder="搜索"
            searchEmptyMessage="没有匹配的选项"
            options={[...subjectOptions(value.subjectKind, dir)]}
            onChange={(option) => patch({ subjectId: option.value })}
          />
        </Field>
      </div>
      <div className="kw-field-row kw-field-row-narrow">
        <Field label="资源类型">
          <Select
            block
            value={value.resourceType}
            options={RESOURCE_TYPES.map((t) => ({ ...t }))}
            onChange={(option) => patch({ resourceType: option.value, resourceId: '' })}
          />
        </Field>
        {value.resourceType === 'knowledge_base' ? (
          <Field label="知识库" error={at === 'resource' ? problem : undefined}>
            <KnowledgeBasePicker
              sources={dir.knowledge}
              value={value.resourceId}
              disabled={value.all}
              onChange={(id) => patch({ resourceId: id })}
            />
          </Field>
        ) : known.length > 0 ? (
          <Field label="资源" error={at === 'resource' ? problem : undefined}>
            <Select
              block
              disabled={value.all}
              value={value.resourceId}
              placeholder="请选择"
              options={[...known]}
              onChange={(option) => patch({ resourceId: option.value })}
            />
          </Field>
        ) : (
          <Field label="资源 ID" error={at === 'resource' ? problem : undefined}>
            {(id) => (
              <Input
                id={id}
                disabled={value.all}
                value={value.resourceId}
                placeholder="资源 ID"
                maxLength={64}
                onChange={(e) => patch({ resourceId: e.target.value })}
              />
            )}
          </Field>
        )}
      </div>
      <div className="kw-field">
        <Checkbox label="全部" checked={value.all} onCheckedChange={(checked) => patch({ all: checked })} />
      </div>
      <Field label="权限" help="使用 < 编辑 < 完全管理，高档包含低档">
        <Select
          block
          value={value.action}
          options={ACTIONS.map((a) => ({ ...a }))}
          onChange={(option) => patch({ action: option.value as Action })}
        />
      </Field>
    </Dialog>
  );
}
