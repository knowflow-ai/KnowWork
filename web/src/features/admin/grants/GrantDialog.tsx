/**
 * 新增授权：选授权对象、资源与权限档位。同一对象对同一资源只保留一档，再授即改档。
 * 对象、资源都是先选类型，再在这一类里多选（部门、组织架构、知识库、工具按层级成树）；不混着选几类，一次授权看得清
 * 授给了谁、授的是什么。几个对象 × 几个资源一次授上，一个事务。
 */
import { useState } from 'react';
import { Checkbox } from '@openai/apps-sdk-ui/components/Checkbox';
import { Input } from '@openai/apps-sdk-ui/components/Input';
import { Select } from '@openai/apps-sdk-ui/components/Select';
import {
  ACTIONS,
  grantMany,
  RESOURCE_TYPES,
  SUBJECT_KINDS,
  type Action,
  type SubjectKind,
} from '../../../api/workspace';
import { Dialog, DialogActions } from '../../../ui/Dialog';
import { Field } from '../../../ui/Field';
import { TreeSelect } from '../../../ui/TreeSelect';
import { useAction } from '../useAction';
import { KEYS } from '../queries';
import type { Directory } from './labels';
import {
  resourceKeys,
  resourceOptions,
  subjectNodes,
  subjectSearchHint,
  validateGrant,
  type GrantInput,
} from './form';
import { KnowledgeBasePicker } from './KnowledgeBasePicker';
import { ToolPicker } from './ToolPicker';

const typeLabel = (type: string): string => RESOURCE_TYPES.find((t) => t.value === type)?.label ?? '资源';

/** 「全部」勾选框的说法：全部什么、含不含以后新增的。知识库是所有知识源下的全部（树里每个知识源另有自己的「全部知识库」）。 */
function allLabel(type: string): string {
  return type === 'knowledge_base'
    ? '全部知识库（所有知识源，含以后新增的）'
    : `全部${typeLabel(type)}（含以后新增的）`;
}

/** 校验不通过时，错误挂在对应的表单项下面。 */
function problemField(problem: string | null): 'subject' | 'resource' | null {
  if (!problem) return null;
  return problem.includes('对象') ? 'subject' : 'resource';
}

export function GrantDialog({ dir, onClose }: { readonly dir: Directory; readonly onClose: () => void }) {
  const [value, setValue] = useState<GrantInput>({
    subjectKind: 'user',
    subjects: [],
    resourceType: 'knowledge_base',
    resourceIds: [],
    all: false,
    action: 'use',
  });
  const [problem, setProblem] = useState<string | null>(null);
  const patch = (next: Partial<GrantInput>) => setValue((v) => ({ ...v, ...next }));
  const action = useAction(
    (input: GrantInput) => grantMany(input.subjects, resourceKeys(input), input.action),
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
            options={SUBJECT_KINDS.filter((k) => k.value !== 'org' || dir.orgDepartments.length > 0).map(
              (k) => ({
                ...k,
              }),
            )}
            onChange={(option) => patch({ subjectKind: option.value as SubjectKind, subjects: [] })}
          />
        </Field>
        <Field label="授权对象" error={at === 'subject' ? problem : undefined}>
          <TreeSelect
            multiple
            aria-label="授权对象"
            nodes={subjectNodes(value.subjectKind, dir)}
            value={value.subjects}
            invalid={at === 'subject'}
            onChange={(subjects) => patch({ subjects })}
            placeholder="请选择，可多选"
            searchPlaceholder={subjectSearchHint(value.subjectKind)}
            emptyMessage="没有匹配的选项"
          />
        </Field>
      </div>
      <div className="kw-field-row kw-field-row-narrow">
        <Field label="资源类型">
          <Select
            block
            value={value.resourceType}
            options={RESOURCE_TYPES.map((t) => ({ ...t }))}
            onChange={(option) => patch({ resourceType: option.value, resourceIds: [] })}
          />
        </Field>
        {value.all ? null : value.resourceType === 'knowledge_base' ? (
          <Field label="知识库" error={at === 'resource' ? problem : undefined}>
            <KnowledgeBasePicker
              sources={dir.knowledge}
              trees={dir.trees}
              value={value.resourceIds}
              invalid={at === 'resource'}
              onChange={(resourceIds) => patch({ resourceIds })}
            />
          </Field>
        ) : value.resourceType === 'tool' ? (
          <Field label="工具" error={at === 'resource' ? problem : undefined}>
            <ToolPicker
              servers={dir.tools}
              value={value.resourceIds}
              invalid={at === 'resource'}
              onChange={(resourceIds) => patch({ resourceIds })}
            />
          </Field>
        ) : known.length > 0 ? (
          <Field label={typeLabel(value.resourceType)} error={at === 'resource' ? problem : undefined}>
            <Select
              block
              multiple
              value={[...value.resourceIds]}
              placeholder="请选择，可多选"
              searchPlaceholder="搜索"
              searchEmptyMessage="没有匹配的选项"
              options={[...known]}
              onChange={(options) => patch({ resourceIds: options.map((option) => option.value) })}
            />
          </Field>
        ) : (
          <Field label="资源 ID" error={at === 'resource' ? problem : undefined}>
            {(id) => (
              <Input
                id={id}
                value={value.resourceIds[0] ?? ''}
                placeholder="资源 ID"
                maxLength={64}
                onChange={(e) => patch({ resourceIds: [e.target.value] })}
              />
            )}
          </Field>
        )}
      </div>
      <div className="kw-field">
        {/* 写明是全部什么：只写「全部」时，勾上它又在上面选了某个目录，看不出到底授的是哪个。
            勾上就不再显示具体选哪个，已选的也清掉 */}
        <Checkbox
          label={allLabel(value.resourceType)}
          checked={value.all}
          onCheckedChange={(checked) => patch({ all: checked, resourceIds: [] })}
        />
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
