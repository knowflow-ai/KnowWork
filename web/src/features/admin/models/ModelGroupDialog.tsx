/** 新建或修改模型分组：授权时按分组授给成员，组里的模型他们就能用。 */
import { Input } from '@openai/apps-sdk-ui/components/Input';
import { Select } from '@openai/apps-sdk-ui/components/Select';
import { Textarea } from '@openai/apps-sdk-ui/components/Textarea';
import {
  createModelGroup,
  updateModelGroup,
  type Model,
  type ModelGroup,
  type ModelGroupInput,
} from '../../../api/models';
import { Dialog, DialogActions } from '../../../ui/Dialog';
import { Field } from '../../../ui/Field';
import { rule, useForm, type Rules } from '../../../ui/form';
import { useAction } from '../useAction';
import { KEYS } from '../queries';

const RULES: Rules<ModelGroupInput> = {
  name: rule.all<ModelGroupInput, 'name'>(
    rule.required('请输入名称'),
    rule.maxLength(100, '名称不能超过 100 个字'),
  ),
  description: rule.maxLength(500, '说明不能超过 500 个字'),
};

export function ModelGroupDialog({
  group,
  models,
  onClose,
}: {
  readonly group?: ModelGroup;
  readonly models: readonly Model[];
  readonly onClose: () => void;
}) {
  const editing = group !== undefined;
  const form = useForm<ModelGroupInput>(
    {
      name: group?.name ?? '',
      description: group?.description ?? '',
      modelIds: [...(group?.modelIds ?? [])],
    },
    RULES,
  );
  const action = useAction(
    (input: ModelGroupInput) => (group ? updateModelGroup(group.id, input) : createModelGroup(input)),
    {
      invalidate: [KEYS.modelGroups],
      success: editing ? '已保存' : '已新建分组',
      failure: '保存没有成功，请检查后重试',
    },
  );
  const submit = async () => {
    const values = form.validate();
    if (values && (await action.run(values))) onClose();
  };
  return (
    <Dialog
      title={editing ? '修改分组' : '新建分组'}
      onClose={onClose}
      onSubmit={() => void submit()}
      footer={<DialogActions okText="保存" loading={action.pending} onCancel={onClose} />}
    >
      <Field label="名称" error={form.errors.name}>
        {(id) => <Input id={id} {...form.text('name')} />}
      </Field>
      <Field label="说明" error={form.errors.description}>
        {(id) => <Textarea id={id} rows={2} autoResize maxRows={4} {...form.text('description')} />}
      </Field>
      <Field label="包含的模型">
        <Select
          multiple
          block
          options={models.map((m) => ({ value: m.id, label: `${m.alias}（${m.name}）` }))}
          value={[...form.values.modelIds]}
          placeholder="选择模型"
          searchPlaceholder="搜索模型"
          searchEmptyMessage="没有匹配的选项"
          onChange={(options) =>
            form.set(
              'modelIds',
              options.map((o) => o.value),
            )
          }
        />
      </Field>
    </Dialog>
  );
}
