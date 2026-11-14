/**
 * 新对话在哪执行（照 ChatGPT 桌面端的「Work in」、Claude 桌面端的执行环境：放在输入框下面靠左，发出第一句以后就定了）。
 * 可选项是来源（sources.ts）：云端，以及扩展登记的（例如桌面端的「本机」）；只有一个时不显示。每项带一句说明。
 */
import { Select } from '@openai/apps-sdk-ui/components/Select';
import type { ConversationSource } from '../sources';

export const PLACE_LABEL = '在哪执行';

const described = (text: string) => <span className="kw-answer-mode-description">{text}</span>;

export function PlaceSelect({
  sources,
  value,
  onChange,
  disabled,
}: {
  readonly sources: readonly ConversationSource[];
  readonly value: ConversationSource;
  readonly onChange: (source: ConversationSource) => void;
  readonly disabled?: boolean;
}) {
  return (
    // 组件库的 Select 不转发 aria-label：包一层 group，读屏与测试都按它找（同回答方式）
    <div role="group" aria-label={PLACE_LABEL} className="kw-place">
      <Select
        variant="ghost"
        size="sm"
        block={false}
        opticallyAlign="start"
        align="start"
        listWidth={300}
        disabled={disabled}
        TriggerStartIcon={value.Icon}
        options={sources.map((source) => ({
          value: source.id,
          label: source.label,
          description: described(source.description),
        }))}
        value={value.id}
        onChange={(option) => {
          const next = sources.find((source) => source.id === option.value);
          if (next) onChange(next);
        }}
      />
    </div>
  );
}

/** 已经建好的对话：只读地写明在哪执行（不在云端的才写）。 */
export function PlaceLabel({ source }: { readonly source: ConversationSource }) {
  return (
    <span className="kw-place-label" aria-label={`${PLACE_LABEL}：${source.label}`}>
      {source.Icon ? <source.Icon /> : null}
      {source.label}
    </span>
  );
}
