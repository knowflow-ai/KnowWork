/**
 * 我的技能的行操作：详情 · 启用/停用，更多里是上传新版本、提交到空间、撤回提交、删除。
 * 提交时可以附一句话给审核的管理员。
 */
import { useRef, useState } from 'react';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { ChevronDown } from '@openai/apps-sdk-ui/components/Icon';
import { Menu } from '@openai/apps-sdk-ui/components/Menu';
import { Textarea } from '@openai/apps-sdk-ui/components/Textarea';
import type { Skill } from '../../api/skills';
import { confirm } from '../../ui/confirmStore';
import { Dialog, DialogActions } from '../../ui/Dialog';
import { Field } from '../../ui/Field';
import { ZipPicker, type ZipPickerHandle } from '../admin/skills/ZipPicker';
import { canSubmit, hasPending } from './model';
import { useMySkillActions } from './queries';

const MAX_NOTE = 500;

function SubmitDialog({ skill, onClose }: { readonly skill: Skill; readonly onClose: () => void }) {
  const { submit } = useMySkillActions();
  const [note, setNote] = useState('');
  const run = async () => {
    if (await submit.run({ skill, note: note.trim() })) onClose();
  };
  return (
    <Dialog
      title={`提交「${skill.name}」到空间`}
      onClose={onClose}
      onSubmit={() => void run()}
      footer={<DialogActions okText="提交" loading={submit.pending} onCancel={onClose} />}
    >
      <p className="kw-secondary-text">
        提交的是当前版本 {skill.publishedVersion}
        。空间管理员审核通过后，会复制一份成为空间技能，按授权给大家用；你这份照旧留着，可以继续改，改好再提交。
      </p>
      <Field label="给审核人的话" help="可不填，例如适合哪些场景、和已有技能有什么不同。">
        {(id) => (
          <Textarea
            id={id}
            rows={3}
            maxLength={MAX_NOTE}
            placeholder="可不填"
            value={note}
            onChange={(event) => setNote(event.target.value)}
          />
        )}
      </Field>
    </Dialog>
  );
}

interface Props {
  readonly skill: Skill;
  readonly onDetail: () => void;
}

export function MySkillActions({ skill, onDetail }: Props) {
  const actions = useMySkillActions();
  const picker = useRef<ZipPickerHandle>(null);
  const [submitting, setSubmitting] = useState(false);
  const confirmDelete = async () => {
    const ok = await confirm({
      title: `删除技能「${skill.name}」？`,
      description: hasPending(skill)
        ? '删除后对话里就用不了它，待审的提交也一起撤回。已经进了空间的那份不受影响。此操作不可撤销。'
        : '删除后对话里就用不了它，以前的版本也一并删除。已经进了空间的那份不受影响。此操作不可撤销。',
      okText: '删除',
      danger: true,
    });
    if (ok) await actions.remove.run(skill);
  };
  return (
    <span className="kw-row-actions">
      <Button color="secondary" variant="ghost" size="sm" onClick={onDetail}>
        详情
      </Button>
      <Button
        color="secondary"
        variant="ghost"
        size="sm"
        loading={actions.toggle.pending}
        onClick={() => void actions.toggle.run({ skill, enabled: !skill.enabled })}
      >
        {skill.enabled ? '停用' : '启用'}
      </Button>
      <Menu>
        <Menu.Trigger>
          <Button color="secondary" variant="ghost" size="sm">
            更多
            <ChevronDown />
          </Button>
        </Menu.Trigger>
        <Menu.Content align="end" minWidth={140}>
          <Menu.Item onSelect={() => picker.current?.open()}>上传新版本</Menu.Item>
          {canSubmit(skill) ? <Menu.Item onSelect={() => setSubmitting(true)}>提交到空间</Menu.Item> : null}
          {hasPending(skill) ? (
            <Menu.Item onSelect={() => void actions.withdraw.run(skill)}>撤回提交</Menu.Item>
          ) : null}
          <Menu.Item className="kw-menu-danger" onSelect={() => void confirmDelete()}>
            删除
          </Menu.Item>
        </Menu.Content>
      </Menu>
      <ZipPicker ref={picker} onPick={(file) => void actions.replace.run({ skill, file })} />
      {submitting ? <SubmitDialog skill={skill} onClose={() => setSubmitting(false)} /> : null}
    </span>
  );
}
