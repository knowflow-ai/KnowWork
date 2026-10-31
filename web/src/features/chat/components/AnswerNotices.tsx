/** 回答下方的说明（events/notices.ts）：一行一条，说明色小字，带链接的在后面接「 · 去管理」。 */
import { Link } from 'react-router';
import type { AnswerNotice } from '../events/notices';

export function AnswerNotices({ notices }: { readonly notices: readonly AnswerNotice[] }) {
  if (notices.length === 0) return null;
  return (
    <div className="kw-answer-notices">
      {notices.map((notice) => (
        <p key={notice.key} className="kw-answer-notice">
          <span>{notice.text}</span>
          {notice.link ? (
            <>
              {' · '}
              <Link className="kw-link" to={notice.link.to}>
                {notice.link.label}
              </Link>
            </>
          ) : null}
        </p>
      ))}
    </div>
  );
}
