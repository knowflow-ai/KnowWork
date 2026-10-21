// 样式必须最先导入：index.css 开头声明了层级顺序（theme < base < components < utilities）。
// 组件库的样式都在 components 层，要是它先于这句声明出现，components 就成了最低的一层，
// Tailwind 的重置会盖掉按钮的底色、圆角、内边距（2026-10-20 按钮全变成方块、菜单项贴边）。
import './styles/index.css';
import './styles/tokens.css';
import './styles/ui.css';
import './styles/app.css';
import './styles/chat.css';
import './styles/preview.css';
import './styles/admin.css';
import './styles/auth.css';
import './styles/trace.css';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { RouterProvider } from 'react-router';
import { Providers } from './app/Providers';
import { createRouter } from './app/router';

const root = document.getElementById('root');
if (root) {
  createRoot(root).render(
    <StrictMode>
      <Providers>
        <RouterProvider router={createRouter()} />
      </Providers>
    </StrictMode>,
  );
}
