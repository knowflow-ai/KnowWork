/**
 * ECharts 的外壳（图表库照成熟问数产品用 Apache ECharts）：按需引入折线、柱状、饼图与 SVG 渲染器，不整包引；
 * 跟着容器宽度重排；卸载时释放。配置由调用方拼好（不在这里拼业务），颜色、字体由调用方从组件库的设计变量里取
 * （chartTokens.ts）。整个文件按需加载（React.lazy），页面上出现图时才下载 ECharts；别处不要静态引它。
 * 给了 onPick 时点图上的柱、点、扇区报出它的分类名（横轴上的那一格），例如结果上点值往下钻。
 */
import { useEffect, useRef } from 'react';
import { BarChart, LineChart, PieChart } from 'echarts/charts';
import { GridComponent, LegendComponent, TitleComponent, TooltipComponent } from 'echarts/components';
import * as echarts from 'echarts/core';
import { SVGRenderer } from 'echarts/renderers';

echarts.use([
  LineChart,
  BarChart,
  PieChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  TitleComponent,
  SVGRenderer,
]);

export default function EChart({
  option,
  height = 260,
  label,
  onPick,
}: {
  /** ECharts 的配置；换了就整份替换（不合并），切换图形时不留上一种的残余 */
  readonly option: Record<string, unknown>;
  readonly height?: number;
  /** 读屏念的说明 */
  readonly label: string;
  /** 点了图上的一项：它的分类名 */
  readonly onPick?: (name: string) => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);
  // 点击只在建图时挂一次，回调换了从这里取最新的
  const pick = useRef(onPick);
  useEffect(() => {
    pick.current = onPick;
  }, [onPick]);
  useEffect(() => {
    if (!box.current) return undefined;
    const instance = echarts.init(box.current, null, { renderer: 'svg' });
    chart.current = instance;
    instance.on('click', (params) => {
      if (params.componentType === 'series' && typeof params.name === 'string') pick.current?.(params.name);
    });
    const resize = new ResizeObserver(() => instance.resize());
    resize.observe(box.current);
    return () => {
      resize.disconnect();
      instance.dispose();
      chart.current = null;
    };
  }, []);
  useEffect(() => {
    chart.current?.setOption(option, true);
  }, [option]);
  return (
    <div
      ref={box}
      className={onPick ? 'kw-echart is-pickable' : 'kw-echart'}
      style={{ height }}
      role="img"
      aria-label={label}
    />
  );
}
