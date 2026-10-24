/**
 * 回答上方的检索条件：模型这一轮按哪些元数据、标签、文件夹筛的，命中几份文档。
 * 筛错了用户能看出来，点 × 撤掉这一条，用同一个问题重新回答（业务服务在检索时去掉它）。
 * 撤掉过的条件划掉显示，下次撤别的条件时一起带上，不会又被加回来。
 */
import { Button } from '@openai/apps-sdk-ui/components/Button';
import { X } from '@openai/apps-sdk-ui/components/Icon';
import { Tooltip } from '@openai/apps-sdk-ui/components/Tooltip';
import type { RunSearch } from '../events/selectors';

interface Props {
  readonly search: RunSearch;
  /** 给了才显示撤销按钮：只有最近一轮、且没有在回答时能撤。 */
  readonly onDrop?: (condition: string) => void;
}

export function SearchConditions({ search, onDrop }: Props) {
  return (
    <div className="kw-search-conditions" role="group" aria-label="检索条件">
      <span className="kw-search-conditions-head">检索条件</span>
      <ul className="kw-search-condition-list">
        {search.conditions.map((condition) => (
          <li key={condition} className="kw-search-condition">
            <span>{condition}</span>
            {onDrop ? (
              <Tooltip content="撤掉这个条件，重新回答" compact>
                <Button
                  color="secondary"
                  variant="ghost"
                  size="2xs"
                  uniform
                  aria-label={`撤掉「${condition}」重新回答`}
                  onClick={() => onDrop(condition)}
                >
                  <X />
                </Button>
              </Tooltip>
            ) : null}
          </li>
        ))}
        {search.removed.map((condition) => (
          <li key={condition} className="kw-search-condition kw-search-condition-removed">
            <del>{condition}</del>
            <span className="kw-search-condition-note">已撤掉</span>
          </li>
        ))}
      </ul>
      <span className="kw-search-conditions-count">命中 {search.documents} 份文档</span>
    </div>
  );
}
