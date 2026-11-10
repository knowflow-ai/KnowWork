/**
 * 新增授权：选授权对象、资源与权限档位。同一对象对同一资源只保留一档，再授即改档。
 * 对象、资源都是先选类型，再在这一类里多选（部门、组织架构、知识库、工具按层级成树）；不混着选几类，一次授权看得清
 * 授给了谁、授的是什么。几个对象 × 几个资源一次授上，一个事务。
 *
 * 资源分两类：能力类（技能、MCP 服务）默认全体成员可用，「使用」只在
 * 各自详情的「给谁用」里改，那里是整体替换；在这里叠加一条「使用」，全体成员能用的时候不起任何作用，管理员却以为收窄了。
 * 所以这里技能、工具都只授「编辑」「管理」（工具这两档目前不带额外能力，MCP 服务只有管理员能维护；两者保持同一套，
 * 以后给 MCP 加「维护者」时直接用）。数据类（知识库、数据源）默认只有管理员，在这里显式授权。
 */
import { useState } from 'react';
import { Checkbox } from '@openai/apps-sdk-ui/components/Checkbox';
import { Input } from '@openai/apps-sdk-ui/components/Input';
import { Select } from '@openai/apps-sdk-ui/components/Select';
import { ACTIONS, grantMany, SUBJECT_KINDS, type Action, type SubjectKind } from '../../../api/workspace';
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

/** 「全部」勾选框的说法：全部什么、含不含以后新增的。知识库是所有知识源下的全部（树里每个知识源另有自己的「全部知识库」）。 */
function allLabel(type: string, label: string): string {
  return type === 'knowledge_base'
    ? '全部知识库（所有知识源，含以后新增的）'
    : `全部${label}（含以后新增的）`;
}

/** 校验不通过时，错误挂在对应的表单项下面。 */
/** 能力类：这里只授「编辑」「管理」，「使用」在各自详情的「给谁用」里改 */
const CAPABILITY_HELP: Readonly<Record<string, string>> = {
  skill: '技能的「使用」在技能详情的「给谁用」里改；这里授「编辑」（改草稿、启停）或「管理」（发布、删除）',
  tool: '工具的「使用」在 MCP 服务详情的「给谁用」里改；这里授「编辑」或「管理」',
};
const isCapability = (type: string) => type in CAPABILITY_HELP;

/** 这类资源能授的几档：能力类不授「使用」；扩展登记时说了只有哪几档的（例如专家只有使用、编辑）照它 */
function actionsOf(type: string, dir: Directory) {
  if (isCapability(type)) return ACTIONS.filter((a) => a.value !== 'use');
  const allowed = dir.resourceTypes.find((t) => t.value === type)?.actions;
  return allowed ? ACTIONS.filter((a) => allowed.includes(a.value)) : ACTIONS;
}

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
  const typeLabel = dir.typeLabels[value.resourceType] ?? '资源';
  const help =
    CAPABILITY_HELP[value.resourceType] ??
    dir.resourceTypes.find((t) => t.value === value.resourceType)?.help;
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
            options={dir.resourceTypes.map((t) => ({ value: t.value, label: t.label }))}
            onChange={(option) => {
              // 换到这类资源授不了的档（能力类不授「使用」，有的扩展类型没有「管理」）：落到它能授的第一档
              const allowed = actionsOf(option.value, dir);
              patch({
                resourceType: option.value,
                resourceIds: [],
                ...(allowed.some((a) => a.value === value.action) ? {} : { action: allowed[0]!.value }),
              });
            }}
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
          <Field label={typeLabel} error={at === 'resource' ? problem : undefined}>
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
          label={allLabel(value.resourceType, typeLabel)}
          checked={value.all}
          onCheckedChange={(checked) => patch({ all: checked, resourceIds: [] })}
        />
      </div>
      <Field
        label="权限"
        help={
          isCapability(value.resourceType)
            ? help
            : help
              ? `${help}；高档包含低档`
              : '使用 < 编辑 < 管理，高档包含低档'
        }
      >
        <Select
          block
          value={value.action}
          options={actionsOf(value.resourceType, dir).map((a) => ({ ...a }))}
          onChange={(option) => patch({ action: option.value as Action })}
        />
      </Field>
    </Dialog>
  );
}
