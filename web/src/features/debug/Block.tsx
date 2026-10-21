/**
 * 运行详情里的一段原文：说明色小标签加灰底框，样式同对话里执行过程的参数与结果。
 * 模型输出、工具结果前后常带空行，去掉首尾空白；长行在框内折行，不撑出横向滚动条。
 */
export function Block({
  label,
  text,
  code = false,
}: {
  readonly label: string;
  readonly text: string;
  readonly code?: boolean;
}) {
  return (
    <div className="kw-trace-block">
      <div className="kw-trace-block-label">{label}</div>
      <pre className={code ? 'is-code' : undefined}>{text.trim() || '（空）'}</pre>
    </div>
  );
}
