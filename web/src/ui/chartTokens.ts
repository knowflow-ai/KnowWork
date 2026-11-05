/**
 * 图表用的颜色与字体：取组件库的设计变量（正文色、次要文字、分割线、底色、字体，以及画数据用的一组色），
 * 算成具体值交给图表库（ECharts 不认 var()）。浅色、深色主题切换时重取，图跟着换。不引图表库，可以静态引。
 *
 * 画数据的色（2026-11-04 照设计稿）：一条线、一组柱子用蓝；几条线、饼图的几块依次用蓝、绿、橙、紫、粉、黄。
 * 不用红：红在界面里表示出错。色值是组件库的基础色板，深浅两种主题同一套。
 */
import { useEffect, useState } from 'react';

export interface ChartTokenValues {
  readonly text: string;
  readonly secondary: string;
  readonly tertiary: string;
  readonly border: string;
  readonly surface: string;
  readonly font: string;
  /** 画数据的一组色，第一个是只有一组数据时用的 */
  readonly palette: readonly string[];
}

const TOKENS: Readonly<Record<keyof Omit<ChartTokenValues, 'palette'>, readonly [string, string]>> = {
  text: ['--color-text', 'rgb(13, 13, 13)'],
  secondary: ['--color-text-secondary', 'rgb(93, 93, 93)'],
  tertiary: ['--color-text-tertiary', 'rgb(143, 143, 143)'],
  border: ['--color-border-subtle', 'rgba(13, 13, 13, 0.08)'],
  surface: ['--color-surface', 'rgb(255, 255, 255)'],
  font: ['--font-sans', 'system-ui, sans-serif'],
};

const PALETTE: readonly (readonly [string, string])[] = [
  ['--blue-400', '#0285ff'],
  ['--green-500', '#00a240'],
  ['--orange-500', '#e25507'],
  ['--purple-500', '#8046d9'],
  ['--pink-500', '#e04c91'],
  ['--yellow-500', '#e0ac00'],
];

/** 把设计变量算成具体的颜色（ECharts 要具体值，不认 var()）：放一个看不见的元素读计算后的样式 */
function readTokens(): ChartTokenValues {
  const probe = document.createElement('span');
  probe.style.display = 'none';
  document.body.appendChild(probe);
  const read = ([name, fallback]: readonly [string, string], property: 'color' | 'fontFamily') => {
    probe.style[property] = `var(${name}, ${fallback})`;
    return getComputedStyle(probe)[property] || fallback;
  };
  const out = {
    text: read(TOKENS.text, 'color'),
    secondary: read(TOKENS.secondary, 'color'),
    tertiary: read(TOKENS.tertiary, 'color'),
    border: read(TOKENS.border, 'color'),
    surface: read(TOKENS.surface, 'color'),
    font: read(TOKENS.font, 'fontFamily'),
    palette: PALETTE.map((token) => read(token, 'color')),
  };
  probe.remove();
  return out;
}

/** 组件库的颜色与字体；主题（data-theme、系统深浅色）变了重取 */
export function useChartTokens(): ChartTokenValues {
  const [tokens, setTokens] = useState(readTokens);
  useEffect(() => {
    const refresh = () => setTokens(readTokens());
    const observer = new MutationObserver(refresh);
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['data-theme', 'class'],
    });
    const media = window.matchMedia?.('(prefers-color-scheme: dark)');
    media?.addEventListener?.('change', refresh);
    return () => {
      observer.disconnect();
      media?.removeEventListener?.('change', refresh);
    };
  }, []);
  return tokens;
}
