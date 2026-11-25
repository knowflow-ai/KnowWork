import { describe, expect, it } from 'vitest';
import { goalCommandText } from './goalText';

/** 官方 dsh-command-goal 的原文（renderGoal 等），照它逐行换成中文 */
describe('/goal 结果换成中文', () => {
  it('设好目标：标题、状态、目标、轮数换成中文；开着自动续跑不提、可用命令那行不要', () => {
    const official = [
      'Goal created',
      'Status: active',
      'Objective: 把报销制度逐条核对一遍',
      'Rounds: 0/20',
      'Activation: armed',
      '',
      'Commands: /goal edit <objective>, /goal pause, /goal clear',
    ].join('\n');
    expect(goalCommandText(official)).toBe(
      [
        '已设好目标，智能体会自己一轮接一轮做下去',
        '状态：进行中',
        '目标：把报销制度逐条核对一遍',
        '自动接着做了 0 / 20 轮',
      ].join('\n'),
    );
  });

  it('受阻带原因；进行中却没开自动续跑写「待继续」', () => {
    const blocked =
      'Goal\nStatus: blocked\nBlocker: round-limit: Goal reached its configured limit of 20 rounds.\nObjective: x\nRounds: 20/20\nActivation: disarmed\n\nCommands: /goal edit <objective>, /goal resume, /goal clear';
    expect(goalCommandText(blocked)).toBe(
      '当前目标\n状态：受阻\n受阻原因：已经自动接着做满 20 轮；要再做，清除后重新设一个目标（改目标不会把轮数清零）\n目标：x\n自动接着做了 20 / 20 轮',
    );
    const waiting = 'Goal\nStatus: active\nObjective: x\nRounds: 3/20\nActivation: disarmed';
    expect(goalCommandText(waiting)).toContain('待继续');
  });

  it('几句固定的话与出错', () => {
    expect(goalCommandText('Goal cleared.')).toBe('目标已清除');
    expect(goalCommandText('No goal to clear.')).toBe('没有要清除的目标');
    expect(
      goalCommandText(
        'No goal is currently set.\nUsage: /goal [<objective>|clear|edit <objective>|pause|resume]',
      ),
    ).toBe('还没有设目标\n用法：/goal 目标内容；/goal edit 新目标、/goal pause、/goal resume、/goal clear');
    expect(
      goalCommandText(
        'No goal is currently set; /goal pause requires one. Usage: /goal [<objective>|clear|edit <objective>|pause|resume]',
      ),
    ).toBe('还没有设目标，不能暂停');
    expect(
      goalCommandText(
        'A goal is already active. Use /goal edit <objective> to change it or /goal clear before replacing it.',
      ),
    ).toBe('已经有一个进行中的目标。要换，先用 /goal edit 改它，或 /goal clear 清除');
    expect(
      goalCommandText(
        'The goal command is not valid for the current state. Run /goal to view available commands.',
      ),
    ).toBe('现在这个状态下不能这样操作目标，打 /goal 看看当前目标');
    expect(goalCommandText('something new from a later version')).toBe('something new from a later version');
  });
});
