/**
 * 表单的值与校验。组件库没有表单，这里只做我们用得到的：每个字段一条校验函数，返回错误文案或 undefined；
 * 第一次提交前不显示错误，提交过一次后边改边校验。
 */
import { useState, type ChangeEvent } from 'react';

export type Check<V, K extends keyof V> = (value: V[K], all: V) => string | undefined;
export type Rules<V> = { readonly [K in keyof V]?: Check<V, K> };
export type Errors<V> = Partial<Record<keyof V, string>>;

export function errorsOf<V>(values: V, rules: Rules<V>): Errors<V> {
  const errors: Errors<V> = {};
  for (const key of Object.keys(rules) as (keyof V)[]) {
    const message = rules[key]?.(values[key], values);
    if (message) errors[key] = message;
  }
  return errors;
}

export function useForm<V extends object>(initial: V, rules: Rules<V> = {}) {
  const [values, setValues] = useState<V>(initial);
  const [submitted, setSubmitted] = useState(false);
  const errors: Errors<V> = submitted ? errorsOf(values, rules) : {};
  const set = <K extends keyof V>(key: K, value: V[K]) =>
    setValues((current) => ({ ...current, [key]: value }));
  return {
    values,
    errors,
    set,
    /** 文本框的 value 与 onChange。 */
    text: <K extends keyof V>(key: K) => ({
      value: String(values[key] ?? ''),
      invalid: Boolean(errors[key]),
      onChange: (event: ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
        set(key, event.target.value as V[K]),
    }),
    /** 提交前调用：有错误返回 null 并开始显示错误，否则返回当前值。 */
    validate: (): V | null => {
      setSubmitted(true);
      return Object.keys(errorsOf(values, rules)).length > 0 ? null : values;
    },
    reset: (next: V = initial) => {
      setValues(next);
      setSubmitted(false);
    },
  };
}

const blank = (value: unknown) =>
  value === undefined ||
  value === null ||
  (typeof value === 'string' && !value.trim()) ||
  (Array.isArray(value) && value.length === 0);

/** 常用校验，按顺序取第一条不通过的。 */
export const rule = {
  required:
    (message: string) =>
    (value: unknown): string | undefined =>
      blank(value) ? message : undefined,
  maxLength:
    (max: number, message: string) =>
    (value: unknown): string | undefined =>
      typeof value === 'string' && value.length > max ? message : undefined,
  email:
    (message: string) =>
    (value: unknown): string | undefined =>
      typeof value === 'string' && value.trim() && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value.trim())
        ? message
        : undefined,
  all:
    <V, K extends keyof V>(...checks: readonly Check<V, K>[]): Check<V, K> =>
    (value, all) => {
      for (const check of checks) {
        const message = check(value, all);
        if (message) return message;
      }
      return undefined;
    },
};
