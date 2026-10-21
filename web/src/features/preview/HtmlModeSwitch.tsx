/** HTML 预览模式切换，放在抽屉顶部「下载」左边；说明放在悬停提示里，不占正文。 */
import { SegmentedControl } from '@openai/apps-sdk-ui/components/SegmentedControl';
import { Tooltip } from '@openai/apps-sdk-ui/components/Tooltip';

type Mode = 'static' | 'scripted';

export function HtmlModeSwitch({
  scripted,
  onChange,
}: {
  readonly scripted: boolean;
  readonly onChange: (scripted: boolean) => void;
}) {
  return (
    <SegmentedControl<Mode>
      aria-label="HTML 预览方式"
      size="sm"
      value={scripted ? 'scripted' : 'static'}
      onChange={(value) => onChange(value === 'scripted')}
    >
      <SegmentedControl.Option value="static">
        <Tooltip content="只显示页面内容，不运行脚本、不加载外部资源" compact>
          <span>安全预览</span>
        </Tooltip>
      </SegmentedControl.Option>
      <SegmentedControl.Option value="scripted">
        <Tooltip content="运行页面里的脚本（如图表）；页面与本系统隔离，读不到你的登录信息" compact>
          <span>运行脚本</span>
        </Tooltip>
      </SegmentedControl.Option>
    </SegmentedControl>
  );
}
