/**
 * 对话的来源：智能体在哪执行（「云端」是业务服务；扩展可以经插槽 chat.sources 登记别的，例如「本机」），
 * id 就是对话的 source 字段。界面只有一套、数据只有一份（界面规范 1.6）：不管在哪执行，列表、历史、实时显示都从业务服务读，
 * 来源只管三件事——
 * - 新对话页输入框下面「在哪执行」的那一项（名字、说明、图标），选中后旁边的控件（Picker，例如选工作文件夹）；
 * - 新对话怎么建、标题与小字；输入框里哪些东西这里用不了（limits：置灰并说明原因，不藏，切换时按钮位置不跳）；
 * - 会话页：发消息、停止、审批交给谁（actions），这里能不能继续；
 * - 这段对话里的文件怎么读、转、存、打开（files）：云端读业务服务，本机由那台电脑读，前端只有一条路径。
 * 只有一个来源（网页、手机）时，新对话页不显示「在哪执行」。
 */
import type { ComponentType, SVGProps } from 'react';
import {
  DEVICE_SOURCE,
  type AnswerMode,
  type QuestionAnswer,
  type ScopeClaim,
  type TurnRequest,
} from '../../api/conversations';
import { Globe } from '@openai/apps-sdk-ui/components/Icon';
import type { InputLimits } from './components/Composer';
import type { SentAttachment } from '../../api/attachments';
import type { Mention } from '../../api/mentions';
import { slotItems } from '../../slots/registry';
import { SERVER_ACTIONS } from './serverActions';
import { SERVER_FILES } from './serverFiles';
import type { FirstMessage } from './hooks/useConversation';
import { startConversation } from './startConversation';

export interface StartInput {
  readonly text: string;
  readonly attachments: readonly SentAttachment[];
  readonly mentions: readonly Mention[];
  readonly answerMode: AnswerMode;
  /** 要挂的东西（例如从专家页开的对话挂这个专家）：不管在哪执行都在建对话时挂上 */
  readonly scope?: ScopeClaim;
}

/**
 * 这段对话里的文件（方案 7.7.3：网页与桌面同一套，照 DSH 官方「对话加路径」）：路径原样交出去，相对路径由执行的一方按这段对话的
 * 工作目录解析。回答里的文件链接、交付卡片都经它读，交给同一个预览抽屉。只在这里能继续这段对话时用；没有的话文件链接只显示文字。
 * 动作不给就不显示对应按钮。出错抛 ApiError（说明给人看）。
 */
export interface ConversationFiles {
  /** 整个文件的原件字节 */
  readonly read: (conversationId: string, path: string) => Promise<ArrayBuffer>;
  /** Word、PPT 等转成的 PDF（给 PDF 阅读器）；不给就只有 PDF 原件能用 PDF 阅读器 */
  readonly pdf?: (conversationId: string, path: string) => Promise<ArrayBuffer>;
  /** 存到本机：桌面端是另存为（弹保存对话框），网页是下载 */
  readonly save?: (conversationId: string, path: string) => Promise<void>;
  /** 用本机程序打开（app）或在文件管理器里显示（reveal）：只有文件就在这台电脑上时有 */
  readonly open?: (conversationId: string, path: string, how: 'app' | 'reveal') => Promise<void>;
  /**
   * 在编辑器里打开：返回编辑器地址，这种文件、这里编辑不了时返回 null；打不开时抛错（说明写在预览上方）。
   * 桌面端的本机对话给；云端对话由扩展经插槽 chat.files.server 给。预览抽屉有它就直接是编辑器
   */
  readonly edit?: (conversationId: string, path: string) => Promise<{ readonly url: string } | null>;
  /**
   * 编辑器说这份文件存了盘（GenOffice 补丁 2 的 `genoffice:saved`：保存、自动保存、智能体存盘都算）：云端要把编辑器里的
   * 文件存回对话，成为这个路径的新一版；本机不用给（文件就在那台电脑的磁盘上）
   */
  readonly saved?: (conversationId: string, path: string) => Promise<void>;
  /**
   * 编辑器里开着的文件，每轮结束时变没变（AI 在这一轮里改了它）、编辑器里有没有没存的改动：path 与 edit 时的写法一样。
   * 返回取消订阅。给了 edit 的来源才给
   */
  readonly editorChanges?: (
    conversationId: string,
    listener: (files: readonly EditedFile[]) => void,
  ) => () => void;
  /** 丢掉编辑器里没存的改动（载入磁盘上被改过的版本之前） */
  readonly discardEdits?: (conversationId: string, path: string) => Promise<void>;
  /**
   * @ 点名工作文件夹里的文件（照官方 @file 补全）：按路径查找词列出文件与文件夹。只有执行的一方看得到文件夹时有
   * （本机对话；云端对话的沙箱目录用户现在看不到，先不给）
   */
  readonly references?: (conversationId: string, query: string) => Promise<readonly FileReference[]>;
  /**
   * 文件面板（照 DSH 官方的工作区文件）：工作文件夹里一层的直接子项，path 相对工作文件夹、根是空串。只有执行的一方看得到
   * 文件夹时有（本机对话）
   */
  readonly list?: (conversationId: string, path: string) => Promise<DirectoryListing>;
  /** 在文件管理器里显示工作文件夹里的一个文件夹（path 同 list，根是空串）：文件面板文件夹的右键 */
  readonly revealFolder?: (conversationId: string, path: string) => Promise<void>;
  /** 跟着一层（list 的 path）的变化，变了调 listener；返回停止跟的函数。给了 list 的来源才可能给 */
  readonly watch?: (conversationId: string, path: string, listener: () => void) => () => void;
}

/** 工作文件夹里的一层（官方 WorkspaceDirectoryListing 加上工作文件夹本身的位置） */
export interface DirectoryListing {
  /** 工作文件夹在执行那台电脑上的位置（标题行显示）；不知道是 null */
  readonly root: string | null;
  /** 这一层相对工作文件夹的路径，根是空串；子项路径是它和名字用 / 拼起来 */
  readonly path: string;
  readonly entries: readonly DirectoryEntry[];
  /** 条目太多，后面的没列出来 */
  readonly truncated: boolean;
}

export interface DirectoryEntry {
  readonly name: string;
  /** 符号链接按它指向的算；other 是既不是文件也不是文件夹的（打不开，照样列出来） */
  readonly type: 'file' | 'directory' | 'other';
  readonly size?: number;
}

/** 一轮结束时编辑器里开着的一个文件：changed 磁盘上变了，unsaved 编辑器里还有没存的改动 */
export interface EditedFile {
  readonly path: string;
  readonly changed: boolean;
  readonly unsaved: boolean;
}

/** 工作文件夹里的一项：insert 是选中后写进消息的文字（照官方语法格式化好的 @路径，带空格的加引号），模型自己去读 */
export interface FileReference {
  readonly path: string;
  readonly kind: 'file' | 'directory';
  readonly insert: string;
}

/**
 * 这段对话的操作交给谁：云端那份交业务服务（serverActions.ts），本机那份由扩展给（交这台电脑上的桌面端）。会话页只认来源给的这一份，
 * 不判断在哪执行。出错抛 ApiError（说明给人看）。
 */
export interface ConversationActions {
  /** 这里能不能继续这段对话：能返回 null，不能返回原因（输入框不可用，占位文字写它） */
  readonly check: (conversationId: string) => Promise<string | null>;
  /**
   * 发一句：turn 和云端发消息是同一个请求，来源原样交给执行的一方，由它交业务服务准备这一轮（多端同一份内核合同）。
   * 返回这条消息的编号（知道的话：业务服务当场给；本机的随事件回来，这里不知道）
   */
  readonly send: (conversationId: string, turn: TurnRequest) => Promise<string | undefined>;
  readonly cancel: (conversationId: string) => Promise<void>;
  /** remember：批准时勾了「本会话内同类操作不再询问」（只在 remembersApprovals 为真时会是 true） */
  readonly decide: (
    conversationId: string,
    approvalId: string,
    allow: boolean,
    remember: boolean,
  ) => Promise<void>;
  /**
   * 审批卡片上给不给「本会话内同类操作不再询问」：执行的一方能记住授权、之后自动放行时才给，不然勾了也没用。
   * 业务服务上执行的对话能（授权记在业务服务、宿主的审批应答者先查它）；不给按不能
   */
  readonly remembersApprovals?: boolean;
  /** 回答提问：questionId 是卡片的编号（官方日志里 ask_user_question 的工具调用 id） */
  readonly answer: (
    conversationId: string,
    questionId: string,
    answers: readonly QuestionAnswer[],
  ) => Promise<void>;
}

export interface ConversationSource {
  /** 对话的 source 字段；业务服务是 server */
  readonly id: string;
  /** 「在哪执行」里的名字与一句说明（例如「云端」「本机」） */
  readonly label: string;
  readonly description: string;
  readonly Icon?: ComponentType<SVGProps<SVGSVGElement>>;
  /** 是不是在业务服务上执行：只有它能预热（宿主上的对话与沙箱容器） */
  readonly server: boolean;
  /** 输入框里这里用不了的东西：置灰并说明原因 */
  readonly limits?: InputLimits;
  /** 选中这个来源时，「在哪执行」旁边的控件（例如选工作文件夹） */
  readonly Picker?: ComponentType;
  /** 会话页这段对话能在这里继续时，输入框下面「在哪执行」旁边的控件（例如本机对话的访问模式） */
  readonly Controls?: ComponentType<{ readonly conversationId: string }>;
  /** 新对话页的标题；不给用默认的「有什么可以帮你？」 */
  readonly welcome?: string;
  /** 输入框下面那行小字；不给用默认的 */
  readonly footnote?: string;
  /** 新对话页还不能发时的原因（例如还没选工作文件夹）：有原因时输入框不可用、占位文字写这句。是 hook，按 hook 的规矩调用 */
  readonly useBlocker?: () => string | null;
  /** 新对话页按了发送：建对话、发第一句。建出来的都是业务服务里的对话，建好进 /chat/<编号> */
  readonly start: (
    input: StartInput,
  ) => Promise<{ readonly id: string; readonly firstMessage: FirstMessage }>;
  /**
   * 拖进输入框的文件写成什么文字（照官方 @file 语法的 @路径，模型自己去读）：执行的一方直接读得到这些文件时给（在用户电脑上
   * 执行的对话，文件就在这台电脑上，不传也不复制）。conversationId 是已有的对话，新对话页为 null。不给时拖进来的照旧当附件
   */
  readonly dropFiles?: (
    files: readonly File[],
    conversationId: string | null,
  ) => Promise<{ readonly mentions: readonly string[]; readonly skipped: readonly string[] }>;
  /** 会话页的操作交给谁（每个来源都要给，云端的是 SERVER_ACTIONS） */
  readonly actions: ConversationActions;
  /** 这段对话里的文件（回答里的文件链接等）怎么读、转、存、打开。不在业务服务上执行的，确认这里能继续时才用；
   *  不给就只显示文字 */
  readonly files?: ConversationFiles;
}

/**
 * 云端对话文件上，扩展补的编辑能力（插槽 chat.files.server，例如在线 Office 编辑）：给了 edit，预览抽屉对能编辑的文件就是
 * 编辑器；没注册时云端文件只能预览。
 */
export type ServerFileEditing = Pick<ConversationFiles, 'edit' | 'saved' | 'editorChanges' | 'discardEdits'>;

let serverFiles: { readonly from: readonly ServerFileEditing[]; readonly files: ConversationFiles } | null =
  null;

/** 云端对话的文件：业务服务那几样，加上扩展补的编辑。插槽每次给同一个数组，合出来的也是同一个对象（页面拿它当依赖） */
function filesOnServer(): ConversationFiles {
  const extra = slotItems('chat.files.server');
  if (serverFiles?.from !== extra) {
    serverFiles = {
      from: extra,
      files: extra.reduce<ConversationFiles>((files, item) => ({ ...files, ...item }), SERVER_FILES),
    };
  }
  return serverFiles.files;
}

export const SERVER_SOURCE: ConversationSource = {
  id: 'server',
  label: '云端',
  description: '在服务器上执行：能用知识库、问数和附件',
  Icon: Globe,
  server: true,
  start: (input) => startConversation(input),
  actions: SERVER_ACTIONS,
  get files() {
    return filesOnServer();
  },
};

export const ELSEWHERE = '这段对话在创建它的电脑上执行，请到那台电脑上继续';
const refuse = async (): Promise<never> => {
  throw new Error(ELSEWHERE);
};

/** 别处只读时的提示：说得出在哪台设备上（扩展登记的 chat.place）就写出来，说不出来照旧 */
export function elsewhere(device: string | null): string {
  return device ? `这段对话在「${device}」上执行，请到那台电脑上继续` : ELSEWHERE;
}

/** 这段在用户设备上执行的对话在哪台设备上；没有扩展说得出、查失败都是 null（提示照旧，不因为它打不开对话） */
export async function deviceOf(conversationId: string): Promise<string | null> {
  const [place] = slotItems('chat.place');
  if (!place) return null;
  try {
    return await place.describe(conversationId);
  } catch {
    return null;
  }
}

/** 在用户设备上执行、而这里没有能接手的（网页、手机、没装对应扩展）：只读。 */
const READ_ONLY: ConversationSource = {
  id: DEVICE_SOURCE,
  label: '本机',
  description: '在创建它的电脑上执行',
  server: false,
  start: refuse,
  actions: {
    check: async (conversationId) => elsewhere(await deviceOf(conversationId)),
    send: refuse,
    cancel: refuse,
    decide: refuse,
    answer: refuse,
  },
};

/** 新对话能选的来源：云端在前，扩展登记的跟在后面。 */
export function startableSources(): readonly ConversationSource[] {
  return [SERVER_SOURCE, ...slotItems('chat.sources')];
}

/** 按对话的 source 字段找来源（会话页用）：在设备上执行而没人接手的只读；IM 平台这类标记照常是业务服务。 */
export function sourceOf(conversationSource: string | undefined): ConversationSource {
  const registered = slotItems('chat.sources').find((source) => source.id === conversationSource);
  if (registered) return registered;
  return conversationSource === DEVICE_SOURCE ? READ_ONLY : SERVER_SOURCE;
}
