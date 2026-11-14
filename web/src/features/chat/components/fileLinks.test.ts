import { describe, expect, it } from 'vitest';
import { fileTarget } from './fileLinks';

describe('回答里的链接是不是文件（照 DSH 官方的判定）', () => {
  it('绝对路径、相对路径、Windows 盘符路径都是文件', () => {
    expect(fileTarget('/work/a/report.md')).toEqual({ path: '/work/a/report.md' });
    expect(fileTarget('./out/a.csv')).toEqual({ path: './out/a.csv' });
    expect(fileTarget('../a.csv')).toEqual({ path: '../a.csv' });
    expect(fileTarget('out/a.csv')).toEqual({ path: 'out/a.csv' });
    expect(fileTarget('C:\\work\\a.txt')).toEqual({ path: 'C:\\work\\a.txt' });
  });

  it('文件名按网址编码解开', () => {
    expect(fileTarget('%E6%8A%A5%E5%91%8A.md')).toEqual({ path: '报告.md' });
    expect(fileTarget('a%20b.md')).toEqual({ path: 'a b.md' });
  });

  it('带行号：:行、:行:列、:行-行，或 #L行-L行', () => {
    expect(fileTarget('src/app.ts:12')).toEqual({ path: 'src/app.ts', line: 12 });
    expect(fileTarget('src/app.ts:12:3')).toEqual({ path: 'src/app.ts', line: 12 });
    expect(fileTarget('src/app.ts:12-20')).toEqual({ path: 'src/app.ts', line: 12 });
    expect(fileTarget('src/app.ts#L5-L9')).toEqual({ path: 'src/app.ts', line: 5 });
    // 行号不合理就只当路径
    expect(fileTarget('src/app.ts:0')).toEqual({ path: 'src/app.ts' });
    expect(fileTarget('src/app.ts#L9-L5')).toEqual({ path: 'src/app.ts' });
  });

  it('网址、邮件、站内锚点、带查询参数的都不是文件', () => {
    expect(fileTarget('https://example.com/a.md')).toBeUndefined();
    expect(fileTarget('mailto:a@example.com')).toBeUndefined();
    expect(fileTarget('#kw-cite-1')).toBeUndefined();
    expect(fileTarget('a.md?x=1')).toBeUndefined();
    expect(fileTarget('')).toBeUndefined();
    // 解不开的编码、控制字符
    expect(fileTarget('%E0%A4%A')).toBeUndefined();
    expect(fileTarget('a%00b.md')).toBeUndefined();
  });
});
