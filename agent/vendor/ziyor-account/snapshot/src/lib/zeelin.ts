export const ZEE_LIN_PROVIDER_ID = 'zeelin' as const
export const ZEE_LIN_PROVIDER_NAME = 'Ziyor' as const
export const ZEE_LIN_BASE_URL = 'https://skill.zeelin.cn/api/v3/anthropic'
export const ZEE_LIN_MESSAGES_URL = `${ZEE_LIN_BASE_URL}/v1/messages`
export const ZEE_LIN_CONNECTION_TEST_TIMEOUT_MS = 120_000

const ZEE_LIN_MODELS = [
  {
    id: 'zeelin-deepseek-v4-pro',
    label: 'Ziyor DeepSeek V4 Pro',
    upstreamName: 'DeepSeek-V4-Pro',
    aliases: ['zeelin-deepseek', 'zl-deepseek'],
  },
  {
    id: 'zeelin-qwen3.6-plus',
    label: 'Ziyor Qwen 3.6 Plus',
    upstreamName: 'qwen3.6-plus',
    aliases: ['zeelin-qwen', 'zeelin-qwen3.6', 'zl-qwen'],
  },
  {
    id: 'zeelin-kimi-k2.6',
    label: 'Ziyor Kimi K2.6',
    upstreamName: 'kimi-k2.6',
    aliases: ['zeelin-kimi', 'zl-kimi'],
  },
  {
    id: 'zeelin-glm-5.1',
    label: 'Ziyor GLM-5.1',
    upstreamName: 'glm-5.1',
    aliases: ['zeelin-glm', 'zl-glm'],
  },
] as const

export const ZEE_LIN_MODEL_ID_TO_UPSTREAM_NAME = Object.fromEntries(
  ZEE_LIN_MODELS.map(model => [model.id, model.upstreamName])
) as Record<(typeof ZEE_LIN_MODELS)[number]['id'], (typeof ZEE_LIN_MODELS)[number]['upstreamName']>

const ZEE_LIN_UPSTREAM_MODEL_OVERRIDES: Record<string, string> = {
  'zeelin-kimi-k2.6': 'kimi-k2.5',
}

export const ZEE_LIN_BUILTIN_MODELS = ZEE_LIN_MODELS.map(({ id, label, aliases }) => ({
  id,
  label,
  provider: ZEE_LIN_PROVIDER_NAME,
  providerId: ZEE_LIN_PROVIDER_ID,
  aliases: [...aliases],
}))

export function isZeeLinModel(modelId?: string): modelId is keyof typeof ZEE_LIN_MODEL_ID_TO_UPSTREAM_NAME {
  return !!modelId && (Object.hasOwn(ZEE_LIN_MODEL_ID_TO_UPSTREAM_NAME, modelId) || modelId.startsWith('zeelin-'))
}

export function getZeeLinUpstreamModel(modelId: string): string {
  const override = ZEE_LIN_UPSTREAM_MODEL_OVERRIDES[modelId]
  if (override) return override
  return ZEE_LIN_MODEL_ID_TO_UPSTREAM_NAME[modelId as keyof typeof ZEE_LIN_MODEL_ID_TO_UPSTREAM_NAME] || modelId
}

export function buildZeeLinAuthHeaders(apiKey: string): Record<string, string> {
  return {
    'x-api-key': apiKey,
    'anthropic-version': '2023-06-01',
  }
}

export function normalizeZeeLinProxyHeaders(headers: Headers): Headers {
  const normalized = new Headers(headers)
  const apiKey = normalized.get('x-api-key') || normalized.get('authorization')?.replace(/^Bearer\s+/i, '')

  normalized.delete('authorization')

  if (apiKey) {
    normalized.set('x-api-key', apiKey)
  }

  if (!normalized.has('anthropic-version')) {
    normalized.set('anthropic-version', '2023-06-01')
  }

  return normalized
}

export function getZeeLinMessagesUrl(baseUrl: string = ZEE_LIN_BASE_URL): string {
  const normalizedBaseUrl = baseUrl
    .replace(/\/v1\/messages\/?$/, '')
    .replace(/\/messages\/?$/, '')
    .replace(/\/$/, '')
  return `${normalizedBaseUrl}/v1/messages`
}
