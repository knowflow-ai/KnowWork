/**
 * 审批卡片（界面规范 2.5）：中性边框，说明要做什么、目标与工具；底部左侧「本会话内同类操作不再询问」，
 * 右侧拒绝/批准。处理过的审批收成一行（结果 · 工具 · 目标），不再占一整张卡片。
 */
import { useState } from 'react';
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { Checkbox } from '@openai/apps-sdk-ui/components/Checkbox';
import { ShieldCheck } from '@openai/apps-sdk-ui/components/Icon';
import type { ApprovalView, StepView } from '../events/types';
import { describeArguments } from '../../../utils/format';
import { toolLabel } from '../events/stepLabels';

interface Props {
  readonly approval: ApprovalView;
  readonly step?: StepView;
  /** remember：批准时勾了「本会话内同类操作不再询问」。 */
  readonly onDecide: (allow: boolean, remember?: boolean) => Promise<void>;
}

const RESOLVED_TEXT: Record<Exclude<ApprovalView['status'], 'pending'>, string> = {
  allowed: '已批准',
  rejected: '已拒绝',
  cancelled: '已失效',
};

function ResolvedLine({
  approval,
  label,
  target,
}: {
  approval: ApprovalView;
  label: string;
  target: string;
}) {
  if (approval.status === 'pending') return null;
  return (
    <div className="kw-approval-resolved" aria-label="已处理的审批">
      <ShieldCheck aria-hidden />
      <span>
        {RESOLVED_TEXT[approval.status]} · {label}
        {target ? ` · ${target}` : ''}
      </span>
    </div>
  );
}

export function ApprovalCard({ approval, step, onDecide }: Props) {
  const [busy, setBusy] = useState<'allow' | 'reject' | null>(null);
  const [remember, setRemember] = useState(false);
  const target = step ? describeArguments(step.arguments, 120) : '';
  // 宿主的审批原因里写的是工具原名（如 mcp__deepwiki__read_wiki_contents），换成和执行步骤一致的显示名
  const label = toolLabel(approval.toolName);
  if (approval.status !== 'pending')
    return <ResolvedLine approval={approval} label={label} target={target} />;

  const decide = async (allow: boolean) => {
    setBusy(allow ? 'allow' : 'reject');
    try {
      await onDecide(allow, allow && remember);
    } finally {
      setBusy(null);
    }
  };
  return (
    <section className="kw-approval" aria-label="需要你批准的操作">
      <div className="kw-approval-head">
        <ShieldCheck className="kw-approval-icon" aria-hidden />
        <div>
          <div className="kw-approval-title">
            {approval.reason
              ? approval.reason.split(approval.toolName).join(label)
              : `要使用工具「${label}」`}
          </div>
          <div className="kw-approval-desc">
            工具：{label}
            {target ? ` · 目标：${target}` : ''}
          </div>
        </div>
      </div>
      <div className="kw-approval-foot">
        <Checkbox
          checked={remember}
          onCheckedChange={setRemember}
          disabled={busy !== null}
          label="本会话内同类操作不再询问"
        />
        <div className="kw-approval-actions">
          <Button
            color="secondary"
            variant="outline"
            onClick={() => void decide(false)}
            loading={busy === 'reject'}
            disabled={busy !== null}
          >
            拒绝
          </Button>
          <Button
            color="primary"
            onClick={() => void decide(true)}
            loading={busy === 'allow'}
            disabled={busy !== null}
          >
            批准
          </Button>
        </div>
      </div>
    </section>
  );
}
