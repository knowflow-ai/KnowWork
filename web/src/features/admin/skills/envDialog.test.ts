import { describe, expect, it } from 'vitest';
import { envChanges } from './skillsModel';

describe('环境变量对话框交给服务端的改动', () => {
  it('填了的给新值，勾了清空的给 null，没动的不交', () => {
    expect(envChanges({ A: 'x', B: '' }, new Set(['C']))).toEqual({ A: 'x', C: null });
    expect(envChanges({ A: 'x' }, new Set(['A']))).toEqual({ A: null });
    expect(envChanges({}, new Set())).toEqual({});
  });
});
