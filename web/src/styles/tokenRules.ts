import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';

/**
 * 样式只用 ChatGPT 设计系统（组件库）的令牌（界面规范第 1 节、第 4 节第 2 条，2026-11-24 全局走查定）：字号、字重、圆角、
 * 文字色、边框直接用组件库的变量，不另起别名、不写死；层级用 tokens.css 的 --kw-z-*；写死的颜色只放 tokens.css
 * （产品自己的链接色、品牌色、看图背板这几个）。商业前端的样式有一份同样的测试。
 */
export interface Offender {
  readonly file: string;
  readonly line: number;
  readonly text: string;
  readonly rule: string;
}

/** var() 里的兜底值不算写死（令牌没定义时才用） */
function withoutVars(value: string): string {
  return value.replace(/var\([^()]*(\([^()]*\))?[^()]*\)/g, '');
}

const RULES: readonly { readonly rule: string; readonly test: (prop: string, value: string) => boolean }[] = [
  {
    rule: '已删的别名：直接用组件库的令牌',
    test: (_prop, value) =>
      /var\(--kw-(text(-secondary|-tertiary)?|font-[a-z]+|radius-(control|card|card-lg|bubble)|border-secondary)\)/.test(
        value,
      ),
  },
  {
    rule: '字号用 --font-text-*-size / --font-heading-*-size',
    test: (prop, value) => prop === 'font-size' && /\d(px|rem)\b/.test(withoutVars(value)),
  },
  {
    rule: '字重用 --font-weight-*',
    test: (prop, value) => prop === 'font-weight' && /^\d+$/.test(value.trim()),
  },
  {
    rule: '圆角用 --radius-*',
    test: (prop, value) =>
      /^border(-[a-z]+)*-radius$/.test(prop) && /(^|\s)([1-9]\d*(px|rem)|\d+%)/.test(withoutVars(value)),
  },
  {
    rule: '上百的层级用 tokens.css 的 --kw-z-*',
    test: (prop, value) => prop === 'z-index' && /\b\d{3,}\b/.test(value),
  },
  {
    rule: '写死的颜色只放 tokens.css',
    test: (prop, value) =>
      !/mask-image$/.test(prop) && /(#[0-9a-fA-F]{3,8}\b|\b(rgb|rgba|hsl|hsla)\()/.test(withoutVars(value)),
  },
];

function cssFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return name === 'node_modules' ? [] : cssFiles(path);
    return name.endsWith('.css') && name !== 'tokens.css' ? [path] : [];
  });
}

export function offenders(root: string): Offender[] {
  return cssFiles(root).flatMap((file) => {
    const lines = readFileSync(file, 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, (comment) => comment.replace(/[^\n]/g, ' '))
      .split('\n');
    return lines.flatMap((text, index) => {
      const match = /^\s*([a-z-]+)\s*:\s*(.+?);?\s*$/.exec(text);
      if (!match?.[1] || !match[2]) return [];
      const [prop, value] = [match[1], match[2]];
      return RULES.filter((r) => r.test(prop, value)).map((r) => ({
        file: file.slice(root.length + 1),
        line: index + 1,
        text: text.trim(),
        rule: r.rule,
      }));
    });
  });
}
