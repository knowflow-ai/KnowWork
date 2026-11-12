/**
 * 网页嵌入渠道的公开配置（ChannelDialog 里平台选「网页嵌入」时显示）：允许嵌入的网站、小窗的名字、开场白、快捷提问、
 * 悬浮按钮的样子、限次。这些不登录就读得到（嵌入页、嵌入脚本要用），所以只放能公开的。
 * 多行的（网站、快捷提问）一行一个。
 */
import { useState } from 'react';
import { Input } from '@openai/apps-sdk-ui/components/Input';
import { RadioGroup } from '@openai/apps-sdk-ui/components/RadioGroup';
import { Textarea } from '@openai/apps-sdk-ui/components/Textarea';
import type { WebOptions } from '../../../api/channels';
import { Field } from '../../../ui/Field';
import { lines, MAX_STARTERS } from './webOptions';

/** 一行一个的多行框：原文留在本地（不然按回车时空行马上被去掉，换不了行），往外只交整理好的列表 */
function LinesInput({
  id,
  value,
  rows,
  placeholder,
  onChange,
}: {
  readonly id: string;
  readonly value: readonly string[];
  readonly rows: number;
  readonly placeholder?: string;
  readonly onChange: (next: readonly string[]) => void;
}) {
  const [text, setText] = useState(() => value.join('\n'));
  return (
    <Textarea
      id={id}
      rows={rows}
      value={text}
      placeholder={placeholder}
      onChange={(event) => {
        setText(event.target.value);
        onChange(lines(event.target.value));
      }}
    />
  );
}

export function WebFields({
  value,
  onChange,
}: {
  readonly value: WebOptions;
  readonly onChange: (next: Partial<WebOptions>) => void;
}) {
  return (
    <>
      <Field
        label="允许嵌入的网站"
        help="一行一个，例如 https://bi.example.com，子域名可以写 https://*.example.com；不写就只能用分享链接打开"
      >
        {(id) => (
          <LinesInput
            id={id}
            rows={3}
            value={value.origins}
            placeholder="https://bi.example.com"
            onChange={(origins) => onChange({ origins })}
          />
        )}
      </Field>
      <div className="kw-field-row kw-field-row-narrow">
        <Field label="小窗名字" help="小窗顶上显示的，空着用渠道名称">
          {(id) => (
            <Input
              id={id}
              value={value.title}
              maxLength={40}
              onChange={(event) => onChange({ title: event.target.value })}
            />
          )}
        </Field>
        <Field label="按钮颜色" help="例如 #1F6FEB，空着用默认">
          {(id) => (
            <Input
              id={id}
              value={value.color}
              maxLength={7}
              placeholder="#1F6FEB"
              onChange={(event) => onChange({ color: event.target.value.trim() })}
            />
          )}
        </Field>
      </div>
      <Field label="开场白" help="还没开始问时显示">
        {(id) => (
          <Textarea
            id={id}
            rows={2}
            maxLength={500}
            value={value.greeting}
            onChange={(event) => onChange({ greeting: event.target.value })}
          />
        )}
      </Field>
      <Field label="快捷提问" help={`一行一个，最多 ${MAX_STARTERS} 个，访客点了就照着问`}>
        {(id) => (
          <LinesInput
            id={id}
            rows={3}
            value={value.starters}
            onChange={(starters) => onChange({ starters })}
          />
        )}
      </Field>
      <Field label="按钮位置">
        <RadioGroup<WebOptions['position']>
          aria-label="按钮位置"
          direction="row"
          value={value.position}
          onChange={(position) => onChange({ position })}
        >
          <RadioGroup.Item value="right">右下角</RadioGroup.Item>
          <RadioGroup.Item value="left">左下角</RadioGroup.Item>
        </RadioGroup>
      </Field>
      {/* 两个限次是同一类设置：两栏等宽，标签一行放得下，两个输入框对齐 */}
      <div className="kw-field-row">
        <Field label="每个访客每分钟最多问" help="认证访问时按人算，防刷">
          {(id) => (
            <Input
              id={id}
              type="number"
              min={1}
              max={120}
              value={String(value.perMinute)}
              onChange={(event) => onChange({ perMinute: Number(event.target.value) })}
            />
          )}
        </Field>
        <Field label="整个入口每天最多问" help="所有人加起来，防刷模型费用">
          {(id) => (
            <Input
              id={id}
              type="number"
              min={1}
              max={1_000_000}
              value={String(value.perDay)}
              onChange={(event) => onChange({ perDay: Number(event.target.value) })}
            />
          )}
        </Field>
      </div>
    </>
  );
}
