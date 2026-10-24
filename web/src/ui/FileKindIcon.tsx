/**
 * 文件类型图标：按扩展名一个带颜色的小方块加白色图案（PDF 红、Word 蓝、Excel 绿、PPT 橙），
 * 和对话里的附件卡片同一套，知识库的文档列表、目录树也用它，让「文件」在哪儿都长一个样。
 */
import { fileKindOf } from './fileKind';
import { FILE_KINDS } from './fileKindStyles';

export function FileKindIcon({ name, size = 20 }: { readonly name: string; readonly size?: number }) {
  const kind = FILE_KINDS[fileKindOf(name)];
  return (
    <span
      className="kw-file-kind-icon"
      style={{ background: kind.color, width: size, height: size }}
      title={kind.label}
      aria-hidden
    >
      {kind.icon}
    </span>
  );
}
