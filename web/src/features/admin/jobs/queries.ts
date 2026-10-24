/** 运行面板的查询：概览每 10 秒刷新一次，任务列表按筛选条件翻页。 */
import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { getJob, getJobsOverview, listJobs, type JobQuery } from '../../../api/jobs';

export const JOBS_KEY = ['system', 'jobs'] as const;
const OVERVIEW_KEY = [...JOBS_KEY, 'overview'] as const;
export const REFRESH_MS = 10_000;

export function useJobsOverview() {
  return useQuery({ queryKey: OVERVIEW_KEY, queryFn: getJobsOverview, refetchInterval: REFRESH_MS });
}

export function useJobList(query: JobQuery) {
  return useInfiniteQuery({
    queryKey: [...JOBS_KEY, 'list', query],
    queryFn: ({ pageParam }) => listJobs(query, pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next ?? undefined,
  });
}

export function useJob(id: string) {
  return useQuery({ queryKey: [...JOBS_KEY, 'detail', id], queryFn: () => getJob(id) });
}
