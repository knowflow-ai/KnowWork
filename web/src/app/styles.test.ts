import { describe, expect, it } from 'vitest';
import main from '../main.tsx?raw';

describe('样式入口', () => {
  it('main.tsx 第一条导入是声明层级顺序的 index.css，组件库样式才不会被 Tailwind 的重置盖掉', () => {
    const first = main.split('\n').find((line: string) => line.startsWith('import '));
    expect(first).toBe("import './styles/index.css';");
  });
});
