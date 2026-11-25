import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { offenders } from './tokenRules';

describe('样式只用组件库的令牌', () => {
  it('开源前端的样式里没有写死的字号、字重、圆角、层级、颜色，也不用已删的别名', () => {
    expect(offenders(join(__dirname, '..'))).toEqual([]);
  });
});
