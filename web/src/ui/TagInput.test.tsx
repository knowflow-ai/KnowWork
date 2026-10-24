import { describe, expect, it } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import userEvent from '../test/user';
import { TAG_DELIMITERS, TagInput, type Tag } from './TagInput';

function Harness({ delimiters }: { readonly delimiters?: string[] }) {
  const [tags, setTags] = useState<Tag[]>([]);
  return (
    <>
      <TagInput id="tags" value={tags} onChange={setTags} delimiters={delimiters} />
      <output aria-label="已收">{tags.map((tag) => tag.value).join('|')}</output>
    </>
  );
}

const input = () => document.getElementById('tags') as HTMLInputElement;
const taken = () => screen.getByLabelText('已收').textContent;

describe('TagInput', () => {
  it('中文输入法组字时按空格选字、按回车上屏都不收标签，组完再回车才收', async () => {
    render(<Harness />);
    await userEvent.type(input(), 'tian');
    fireEvent.keyDown(input(), { key: ' ', isComposing: true });
    fireEvent.keyDown(input(), { key: 'Enter', keyCode: 229 });
    expect(taken()).toBe('');
    expect(input().value).toBe('tian');
    fireEvent.keyDown(input(), { key: 'Enter' });
    expect(taken()).toBe('tian');
  });

  it('回车、英文逗号、中文逗号收标签；空格不拆，值里可以带空格', async () => {
    render(<Harness />);
    await userEvent.type(input(), 'Q3 报告{Enter}华东，华南,');
    expect(taken()).toBe('Q3 报告|华东|华南');
  });

  it('调用方可以另给分隔符（ID 列表里加上空格）', async () => {
    render(<Harness delimiters={[...TAG_DELIMITERS, ' ']} />);
    await userEvent.type(input(), 'ds1 ds2 ');
    expect(taken()).toBe('ds1|ds2');
  });
});
