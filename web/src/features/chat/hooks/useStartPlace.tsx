/**
 * 新对话的「在哪执行」：可选的来源、选中的那个、它说还不能发的原因（例如还没选工作文件夹），和输入框下面那一排
 * （「在哪执行」与来源自己的控件；只有一个来源时没有）。新对话页和扩展的新对话页（例如从专家页开的对话）共用这一份，
 * 不各写一套：哪里新开对话都能选在哪执行。
 */
import { useState, type ReactNode } from 'react';
import { useSearchParams } from 'react-router';
import { PlaceSelect } from '../components/PlaceSelect';
import { SERVER_SOURCE, startableSources, type ConversationSource } from '../sources';

const noBlocker = () => null;

export interface StartPlace {
  readonly source: ConversationSource;
  /** 选中的来源说还不能发的原因；能发是 null */
  readonly blocker: string | null;
  /** 输入框下面那一排；只有一个来源、或 hidden 时是 undefined */
  readonly below: ReactNode;
}

/** hidden：这会儿不显示那一排（例如按了发送、对话还在建） */
export function useStartPlace(hidden = false): StartPlace {
  // 可选的来源在装配时就定了（插槽登记），同一页里不变；默认云端，每次新对话都从云端开始（不沿用上次的选择）。
  // 地址上带 place=<来源> 的从那个来源开始（例如侧栏工作区的「新对话」：本机，文件夹由扩展先选好）
  const [sources] = useState(startableSources);
  const [params] = useSearchParams();
  const place = params.get('place');
  // 地址给的来源是默认值；用户在这一页手动换过的记下是在哪个 place 下换的，place 变了（又从侧栏点进来）就回到地址给的
  const [chosen, setChosen] = useState<{
    readonly place: string | null;
    readonly source: ConversationSource;
  }>();
  const source =
    chosen && chosen.place === place
      ? chosen.source
      : (sources.find((item) => item.id === place) ?? SERVER_SOURCE);
  const setSource = (next: ConversationSource) => setChosen({ place, source: next });
  // 每个来源的 hook 都调（来源列表不变，hook 顺序稳定），取选中的那个
  const blockers = sources.map((item) => (item.useBlocker ?? noBlocker)());
  const blocker = blockers[sources.indexOf(source)] ?? null;
  const below =
    sources.length > 1 && !hidden ? (
      <>
        <PlaceSelect sources={sources} value={source} onChange={setSource} />
        {source.Picker ? <source.Picker /> : null}
      </>
    ) : undefined;
  return { source, blocker, below };
}
