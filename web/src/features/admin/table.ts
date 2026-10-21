/** 管理页表格的本地搜索。 */
export function includesText(haystack: readonly (string | null | undefined)[], keyword: string): boolean {
  const needle = keyword.trim().toLowerCase();
  if (!needle) return true;
  return haystack.some((value) => value?.toLowerCase().includes(needle));
}
