/** API Key 有效期的档位与换算。 */
export const EXPIRY_OPTIONS = [
  { value: 0, label: '永不过期' },
  { value: 30, label: '30 天' },
  { value: 90, label: '90 天' },
  { value: 365, label: '1 年' },
] as const;

export function expiryFrom(days: number, now: Date = new Date()): string | null {
  return days > 0 ? new Date(now.getTime() + days * 86_400_000).toISOString() : null;
}
