/** 厂商表单：密钥只能写不能读，修改时区分「换新」「清除」「不改」。 */
import type { ProviderInput } from '../../../api/models';

export interface Values {
  readonly name: string;
  readonly baseUrl: string;
  readonly apiKey?: string;
  readonly enabled: boolean;
}

/** 修改时：填了新密钥就换；勾了清除就传空串；都没有就不改。 */
export function providerInput(values: Values, editing: boolean, clearKey: boolean): ProviderInput {
  const typed = values.apiKey?.trim() ?? '';
  const apiKey = editing ? (typed ? typed : clearKey ? '' : undefined) : typed;
  return { name: values.name, baseUrl: values.baseUrl, enabled: values.enabled, apiKey };
}
