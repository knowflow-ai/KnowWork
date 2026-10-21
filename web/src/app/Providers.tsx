/** 全局 Provider：ChatGPT 组件库（链接用 react-router）、TanStack Query，以及全局的提示条与确认框。 */
import type { ReactNode } from 'react';
import { AppsSDKUIProvider } from '@openai/apps-sdk-ui/components/AppsSDKUIProvider';
import { QueryClientProvider, type QueryClient } from '@tanstack/react-query';
import { Link } from 'react-router';
import { ConfirmHost } from '../ui/confirm';
import { Toaster } from '../ui/Toaster';
import { queryClient } from './queryClient';

export function Providers({
  children,
  client = queryClient,
}: {
  readonly children: ReactNode;
  readonly client?: QueryClient;
}) {
  return (
    <AppsSDKUIProvider linkComponent={Link}>
      <QueryClientProvider client={client}>
        <div className="kw-root">{children}</div>
        <Toaster />
        <ConfirmHost />
      </QueryClientProvider>
    </AppsSDKUIProvider>
  );
}
