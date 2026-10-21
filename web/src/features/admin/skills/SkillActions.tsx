/** 技能行操作：详情 · 发布 · 启用/停用，更多里是上传新版本与删除。操作列一律文字按钮，最多露三个。 */
import { useRef, useState } from 'react';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { ChevronDown } from '@openai/apps-sdk-ui/components/Icon';
import { Input } from '@openai/apps-sdk-ui/components/Input';
import { Menu } from '@openai/apps-sdk-ui/components/Menu';
import type { Skill } from '../../../api/skills';
import { confirm } from '../../../ui/confirmStore';
import { Dialog, DialogActions } from '../../../ui/Dialog';
import { Field } from '../../../ui/Field';
import { useSkillActions } from './queries';
import { canPublish, defaultVersion } from './skillsModel';
import { ZipPicker, type ZipPickerHandle } from './ZipPicker';

const VERSION = /^[0-9A-Za-z][0-9A-Za-z.+-]{0,63}$/;

function PublishDialog({ skill, onClose }: { readonly skill: Skill; readonly onClose: () => void }) {
  const { publish } = useSkillActions();
  const [version, setVersion] = useState(defaultVersion(skill));
  const [problem, setProblem] = useState<string>();
  const submit = async () => {
    const value = version.trim();
    const invalid = value && !VERSION.test(value) ? '版本号只能用字母、数字和 . + -' : undefined;
    setProblem(invalid);
    if (invalid) return;
    if (await publish.run({ skill, version: value || undefined })) onClose();
  };
  return (
    <Dialog
      title={`发布技能「${skill.name}」`}
      onClose={onClose}
      onSubmit={() => void submit()}
      footer={<DialogActions okText="发布" loading={publish.pending} onCancel={onClose} />}
    >
      <Field
        label="版本号"
        help="留空则自动编号。发布后，新开的对话用这个版本；已经在进行的对话不受影响。"
        error={problem}
      >
        {(id) => (
          <Input
            id={id}
            placeholder="例如 1.0.0"
            maxLength={64}
            invalid={Boolean(problem)}
            value={version}
            onChange={(e) => setVersion(e.target.value)}
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

export function SkillActions({ skill, onDetail }: Props) {
  const actions = useSkillActions();
  const picker = useRef<ZipPickerHandle>(null);
  const [publishing, setPublishing] = useState(false);
  const confirmDelete = async () => {
    const ok = await confirm({
      title: `删除技能「${skill.name}」？`,
      description: '删除后成员在对话里就用不了它，已发布的版本也一并删除，此操作不可撤销。',
      okText: '删除',
      danger: true,
    });
    if (ok) await actions.remove.run(skill);
  };
  const toggle = () => void actions.toggle.run({ skill, enabled: !skill.enabled });
  return (
    <span className="kw-row-actions">
      <Button color="secondary" variant="ghost" size="sm" onClick={onDetail}>
        详情
      </Button>
      {canPublish(skill) ? (
        <Button color="secondary" variant="ghost" size="sm" onClick={() => setPublishing(true)}>
          发布
        </Button>
      ) : (
        <Button color="secondary" variant="ghost" size="sm" loading={actions.toggle.pending} onClick={toggle}>
          {skill.enabled ? '停用' : '启用'}
        </Button>
      )}
      <Menu>
        <Menu.Trigger>
          <Button color="secondary" variant="ghost" size="sm">
            更多
            <ChevronDown />
          </Button>
        </Menu.Trigger>
        <Menu.Content align="end" minWidth={140}>
          {canPublish(skill) && skill.status === 'published' ? (
            <Menu.Item onSelect={toggle}>{skill.enabled ? '停用' : '启用'}</Menu.Item>
          ) : null}
          <Menu.Item onSelect={() => picker.current?.open()}>上传新版本</Menu.Item>
          <Menu.Item className="kw-menu-danger" onSelect={() => void confirmDelete()}>
            删除
          </Menu.Item>
        </Menu.Content>
      </Menu>
      <ZipPicker ref={picker} onPick={(file) => void actions.replace.run({ skill, file })} />
      {publishing ? <PublishDialog skill={skill} onClose={() => setPublishing(false)} /> : null}
    </span>
  );
}
