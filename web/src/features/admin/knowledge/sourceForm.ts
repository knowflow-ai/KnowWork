/** 知识源表单的值与接口入参之间的转换。 */
import type { AuthMode, KnowledgeSource, SourceInput } from '../../../api/knowledge';

export interface Values {
  readonly name: string;
  readonly baseUrl: string;
  readonly enabled: boolean;
  readonly authMode: AuthMode;
  readonly serviceKey?: string;
  readonly accountKey?: string;
  readonly datasetIds: readonly string[];
  readonly webBaseUrl?: string;
  readonly similarityThreshold: number;
  readonly vectorSimilarityWeight: number;
  readonly timeoutSeconds: number;
}

export function toSourceInput(values: Values): SourceInput {
  return {
    name: values.name ?? '',
    baseUrl: values.baseUrl ?? '',
    enabled: values.enabled,
    serviceKey: values.serviceKey?.trim() || undefined,
    accountKey: values.accountKey?.trim() || undefined,
    config: {
      authMode: values.authMode,
      datasetIds: (values.datasetIds ?? []).map((id) => id.trim()).filter(Boolean),
      webBaseUrl: values.webBaseUrl?.trim() || null,
      similarityThreshold: values.similarityThreshold,
      vectorSimilarityWeight: values.vectorSimilarityWeight,
      timeoutSeconds: values.timeoutSeconds,
    },
  };
}

export function initialValues(source?: KnowledgeSource): Values {
  const config = source?.detail?.config;
  return {
    name: source?.name ?? '',
    baseUrl: source?.detail?.baseUrl ?? '',
    enabled: source?.enabled ?? true,
    authMode: config?.authMode ?? 'shared',
    datasetIds: [...(config?.datasetIds ?? [])],
    webBaseUrl: config?.webBaseUrl ?? '',
    similarityThreshold: config?.similarityThreshold ?? 0.2,
    vectorSimilarityWeight: config?.vectorSimilarityWeight ?? 0.3,
    timeoutSeconds: config?.timeoutSeconds ?? 15,
  };
}
