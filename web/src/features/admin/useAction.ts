/** 管理页的变更：成功后刷新相关查询并提示，失败时提示可读原因。 */
import { useMutation, useQueryClient, type QueryKey } from '@tanstack/react-query';
import { readableError } from '../../api/http';
import { toast } from '../../ui/toastStore';

/** 变更：成功后刷新相关查询并提示，失败时提示可读原因。返回 mutateAsync 的包装，失败时不再向外抛。 */
export function useAction<Input>(
  fn: (input: Input) => Promise<unknown>,
  {
    invalidate,
    success,
    failure,
  }: { readonly invalidate: readonly QueryKey[]; readonly success?: string; readonly failure: string },
) {
  const client = useQueryClient();
  const mutation = useMutation({ mutationFn: fn });
  const run = async (input: Input): Promise<boolean> => {
    try {
      await mutation.mutateAsync(input);
      await Promise.all(invalidate.map((queryKey) => client.invalidateQueries({ queryKey })));
      if (success) toast.success(success);
      return true;
    } catch (error) {
      toast.error(readableError(error, failure));
      return false;
    }
  };
  return { run, pending: mutation.isPending };
}
