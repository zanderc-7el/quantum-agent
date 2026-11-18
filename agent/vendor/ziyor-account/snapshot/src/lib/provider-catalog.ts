import { DEEPSEEK_BASE_URL } from './deepseek'
import { MIMO_BASE_URL } from './mimo'
import { OPENAI_BASE_URL } from './openai'
import { getZeeLinUpstreamModel } from './zeelin'
import fs from 'node:fs'
import path from 'node:path'

export const ZEE_LIN_MODELS_V2_URL = 'https://skill.zeelin.cn/api/v3/models_v2'
export const LOCAL_PROVIDER_CATALOG_PATH = '.local/provider-models-v2.json'
const LOCAL_PROVIDER_CATALOG_TEST_FLAG = 'ZEELIN_ENABLE_LOCAL_PROVIDER_CATALOG'
const PROVIDER_CATALOG_FETCH_TIMEOUT_MS = 8_000
const ZEE_LIN_TOKEN_BASE_URL = 'https://skill.zeelin.cn/api/v3/anthropic'

export type BillingChannelId = 'token' | 'coding'
export type EndpointProtocol = 'anthropic_messages' | 'openai_chat_completions' | 'openai_responses'
export type EndpointAuth = 'x-api-key' | 'bearer'

export interface ProviderEndpoint {
  key: string
  baseUrl: string
  protocol: EndpointProtocol
  auth: EndpointAuth
}

export const PROVIDER_ENDPOINTS: Record<string, ProviderEndpoint> = {
  zeelin_token: { key: 'zeelin_token', baseUrl: ZEE_LIN_TOKEN_BASE_URL, protocol: 'anthropic_messages', auth: 'x-api-key' },
  openai_token: { key: 'openai_token', baseUrl: OPENAI_BASE_URL, protocol: 'openai_responses', auth: 'bearer' },
  deepseek_token: { key: 'deepseek_token', baseUrl: DEEPSEEK_BASE_URL, protocol: 'anthropic_messages', auth: 'x-api-key' },
  doubao_token: { key: 'doubao_token', baseUrl: 'https://ark.cn-beijing.volces.com/api/v3', protocol: 'openai_chat_completions', auth: 'bearer' },
  doubao_coding: { key: 'doubao_coding', baseUrl: 'https://ark.cn-beijing.volces.com/api/coding', protocol: 'anthropic_messages', auth: 'bearer' },
  qwen_token: { key: 'qwen_token', baseUrl: 'https://dashscope.aliyuncs.com/apps/anthropic', protocol: 'anthropic_messages', auth: 'bearer' },
  qwen_coding: { key: 'qwen_coding', baseUrl: 'https://token-plan.cn-beijing.maas.aliyuncs.com/apps/anthropic', protocol: 'anthropic_messages', auth: 'bearer' },
  moonshot_token: { key: 'moonshot_token', baseUrl: 'https://api.moonshot.cn/anthropic', protocol: 'anthropic_messages', auth: 'bearer' },
  moonshot_coding: { key: 'moonshot_coding', baseUrl: 'https://api.kimi.com/coding', protocol: 'anthropic_messages', auth: 'bearer' },
  zhipu_token: { key: 'zhipu_token', baseUrl: 'https://open.bigmodel.cn/api/anthropic', protocol: 'anthropic_messages', auth: 'bearer' },
  zhipu_coding: { key: 'zhipu_coding', baseUrl: 'https://open.bigmodel.cn/api/anthropic', protocol: 'anthropic_messages', auth: 'bearer' },
  minimax_token: { key: 'minimax_token', baseUrl: 'https://api.minimaxi.com/anthropic', protocol: 'anthropic_messages', auth: 'x-api-key' },
  minimax_coding: { key: 'minimax_coding', baseUrl: 'https://api.minimaxi.com/anthropic', protocol: 'anthropic_messages', auth: 'x-api-key' },
  mimo_token: { key: 'mimo_token', baseUrl: MIMO_BASE_URL, protocol: 'anthropic_messages', auth: 'x-api-key' },
  mimo_coding: { key: 'mimo_coding', baseUrl: 'https://token-plan-cn.xiaomimimo.com/anthropic', protocol: 'anthropic_messages', auth: 'x-api-key' },
}

export interface CatalogModel {
  id: string
  label: string
  provider: string
  providerId: string
  aliases?: string[]
  contextWindow?: string
  maxOutput?: string
  billingChannel: BillingChannelId
  billingChannelLabel: string
  endpointKey: string
  upstreamName: string
  capabilities?: {
    thinking?: boolean
    maxContextTokens?: number
    maxOutputTokens?: number
  }
}

export interface CatalogChannel {
  id: BillingChannelId
  label: string
  planName: string
  endpointKey: string
  endpoint: ProviderEndpoint
  defaultModelId?: string
  models: CatalogModel[]
}

export interface CatalogProvider {
  providerId: string
  providerName: string
  enabled: boolean
  defaultBillingChannel: BillingChannelId
  billingChannels: CatalogChannel[]
}

export interface NormalizedProviderCatalog {
  schemaVersion: 2
  updatedAt?: string
  cacheTtlSeconds: number
  providers: CatalogProvider[]
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value)
}

function asBillingChannel(value: unknown): BillingChannelId | null {
  return value === 'token' || value === 'coding' ? value : null
}

function normalizeAliases(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value.filter((item): item is string => typeof item === 'string' && item.trim().length > 0)
}

function normalizeCapabilities(value: unknown): CatalogModel['capabilities'] | undefined {
  if (!isRecord(value)) return undefined
  return {
    thinking: typeof value.thinking === 'boolean' ? value.thinking : undefined,
    maxContextTokens: typeof value.maxContextTokens === 'number' ? value.maxContextTokens : undefined,
    maxOutputTokens: typeof value.maxOutputTokens === 'number' ? value.maxOutputTokens : undefined,
  }
}

function normalizeProviderModels(
  providerId: string,
  providerName: string,
  billingChannel: BillingChannelId,
  billingChannelLabel: string,
  endpointKey: string,
  rawModels: unknown,
): CatalogModel[] {
  if (!Array.isArray(rawModels)) return []

  const seenChannelModelIds = new Set<string>()
  return rawModels
    .filter(isRecord)
    .map((raw): (CatalogModel & { sort: number }) | null => {
      if (raw.enabled !== true) return null
      if (typeof raw.id !== 'string' || !raw.id.trim()) return null
      if (seenChannelModelIds.has(raw.id)) return null
      if (typeof raw.label !== 'string' || !raw.label.trim()) return null

      seenChannelModelIds.add(raw.id)

      return {
        id: raw.id,
        label: raw.label,
        provider: providerName,
        providerId,
        aliases: normalizeAliases(raw.aliases),
        contextWindow: typeof raw.contextWindow === 'string' ? raw.contextWindow : undefined,
        maxOutput: typeof raw.maxOutput === 'string' ? raw.maxOutput : undefined,
        billingChannel,
        billingChannelLabel,
        endpointKey,
        upstreamName: typeof raw.upstreamName === 'string' && raw.upstreamName.trim() ? raw.upstreamName : raw.id,
        capabilities: normalizeCapabilities(raw.capabilities),
        sort: typeof raw.sort === 'number' ? raw.sort : Number.MAX_SAFE_INTEGER,
      }
    })
    .filter((model): model is CatalogModel & { sort: number } => !!model)
    .sort((a, b) => a.sort - b.sort || a.label.localeCompare(b.label))
    .map(({ sort: _sort, ...model }) => model)
}

function normalizeV2(raw: Record<string, unknown>): NormalizedProviderCatalog {
  const providers = Array.isArray(raw.providers) ? raw.providers : []
  const normalizedProviders = providers
    .filter(isRecord)
    .map((provider): CatalogProvider | null => {
      if (provider.enabled !== true) return null
      if (typeof provider.providerId !== 'string' || !provider.providerId.trim()) return null
      if (typeof provider.providerName !== 'string' || !provider.providerName.trim()) return null

      const providerId = provider.providerId
      const providerName = provider.providerName
      const defaultBillingChannel = asBillingChannel(provider.defaultBillingChannel) || 'token'
      const billingChannels = Array.isArray(provider.billingChannels)
        ? provider.billingChannels
        : (providerId === 'zeelin' && Array.isArray(provider.models)
          ? [
            {
              id: 'token',
              label: 'Token 通道',
              planName: 'Ziyor Token Plan',
              endpointKey: 'zeelin_token',
              defaultModelId: provider.defaultModelId,
              models: provider.models,
            },
          ]
          : [])
      const channels = billingChannels
        .filter(isRecord)
        .map((channel): CatalogChannel | null => {
          const id = asBillingChannel(channel.id)
          if (!id) return null
          if (typeof channel.endpointKey !== 'string') return null

          const endpoint = PROVIDER_ENDPOINTS[channel.endpointKey]
          if (!endpoint) return null

          const label = typeof channel.label === 'string' && channel.label.trim()
            ? channel.label
            : (id === 'coding' ? 'Coding 通道' : 'Token 通道')
          const models = normalizeProviderModels(
            providerId,
            providerName,
            id,
            label,
            endpoint.key,
            channel.models,
          )

          if (!Array.isArray(channel.models) || channel.models.length === 0) return null
          if (models.length === 0) return null

          const defaultModelId = typeof channel.defaultModelId === 'string' && models.some(model => model.id === channel.defaultModelId)
            ? channel.defaultModelId
            : models[0]?.id

          return {
            id,
            label,
            planName: typeof channel.planName === 'string' && channel.planName.trim()
              ? channel.planName
              : `${provider.providerName} ${id === 'coding' ? 'Coding' : 'Token'} Plan`,
            endpointKey: endpoint.key,
            endpoint,
            defaultModelId,
            models,
          }
        })
        .filter((channel): channel is CatalogChannel => !!channel)

      if (channels.length === 0) return null

      const normalizedDefaultBillingChannel = channels.some(channel => channel.id === defaultBillingChannel)
        ? defaultBillingChannel
        : channels[0].id

      return {
        providerId,
        providerName,
        enabled: true,
        defaultBillingChannel: normalizedDefaultBillingChannel,
        billingChannels: channels,
      }
    })
    .filter((provider): provider is CatalogProvider => !!provider)

  return {
    schemaVersion: 2,
    updatedAt: typeof raw.updatedAt === 'string' ? raw.updatedAt : undefined,
    cacheTtlSeconds: typeof raw.cacheTtlSeconds === 'number' && raw.cacheTtlSeconds > 0 ? raw.cacheTtlSeconds : 3600,
    providers: normalizedProviders,
  }
}

function normalizeZeeLinV1(raw: Record<string, unknown>): NormalizedProviderCatalog {
  return normalizeV2({
    schemaVersion: 2,
    updatedAt: raw.updatedAt,
    cacheTtlSeconds: raw.cacheTtlSeconds,
    providers: [
      {
        providerId: 'zeelin',
        providerName: 'Ziyor（智灵动力）',
        enabled: true,
        defaultBillingChannel: 'token',
        billingChannels: [
          {
            id: 'token',
            label: 'Token 通道',
            planName: 'Ziyor Token Plan',
            endpointKey: 'zeelin_token',
            defaultModelId: raw.defaultModelId,
            models: raw.models,
          },
        ],
      },
    ],
  })
}

export function normalizeProviderCatalog(raw: unknown): NormalizedProviderCatalog {
  if (!isRecord(raw)) throw new Error('Invalid provider catalog')
  if (raw.schemaVersion === 2) return normalizeV2(raw)
  if (raw.schemaVersion === 1 && raw.providerId === 'zeelin') return normalizeZeeLinV1(raw)
  throw new Error('Unsupported provider catalog schema')
}

const FALLBACK_MODELS: Record<string, Array<{
  id: string
  label: string
  aliases: string[]
  contextWindow?: string
  maxOutput?: string
}>> = {
  zeelin: [
    { id: 'zeelin-deepseek-v4-pro', label: 'Ziyor DeepSeek V4 Pro', aliases: ['zeelin-deepseek', 'zl-deepseek'] },
    { id: 'zeelin-qwen3.6-plus', label: 'Ziyor Qwen 3.6 Plus', aliases: ['zeelin-qwen', 'zeelin-qwen3.6', 'zl-qwen'] },
    { id: 'zeelin-kimi-k2.6', label: 'Ziyor Kimi K2.6', aliases: ['zeelin-kimi', 'zl-kimi'] },
    { id: 'zeelin-glm-5.1', label: 'Ziyor GLM-5.1', aliases: ['zeelin-glm', 'zl-glm'] },
  ],
  openai: [
    { id: 'gpt-5.5', label: 'GPT-5.5', aliases: ['gpt', 'gpt5.5', 'openai'] },
    { id: 'gpt-5.4', label: 'GPT-5.4', aliases: ['gpt5.4', 'openai-frontier'] },
    { id: 'chat-latest', label: 'Chat Latest', aliases: ['chatgpt', 'chat-latest'] },
  ],
  deepseek: [
    { id: 'deepseek-v4-pro', label: 'DeepSeek V4 Pro', aliases: ['deepseek', 'deepseek-pro'] },
    { id: 'deepseek-v4-flash', label: 'DeepSeek V4 Flash', aliases: ['deepseek-flash'] },
  ],
  doubao: [
    { id: 'doubao-seed-2.0-code', label: 'Doubao Seed 2.0 Code', aliases: ['doubao-code'] },
    { id: 'doubao-seed-2.0-pro', label: 'Doubao Seed 2.0 Pro', aliases: ['doubao-pro'] },
    { id: 'doubao-seed-2.0-lite', label: 'Doubao Seed 2.0 Lite', aliases: ['doubao-lite'] },
    { id: 'ark-code-latest', label: 'Ark Code Latest', aliases: ['ark-code'] },
  ],
  qwen: [
    { id: 'qwen3.7-max', label: 'Qwen 3.7 Max', aliases: ['qwen-max'] },
    { id: 'qwen3.7-plus', label: 'Qwen 3.7 Plus', aliases: ['qwen-plus'] },
    { id: 'qwen3.6-plus', label: 'Qwen 3.6 Plus', aliases: ['qwen3.6-plus'] },
    { id: 'deepseek-v4-pro', label: 'DeepSeek V4 Pro', aliases: ['deepseek', 'deepseek-pro'] },
    { id: 'deepseek-v4-flash', label: 'DeepSeek V4 Flash', aliases: ['deepseek-flash'] },
  ],
  moonshot: [
    { id: 'kimi-k2.5', label: 'Kimi K2.5', aliases: ['kimi'] },
    { id: 'kimi-k2.6', label: 'Kimi K2.6', aliases: ['kimi-k2.6'] },
    { id: 'kimi-k2-thinking', label: 'Kimi K2 Thinking', aliases: ['kimi-thinking'] },
    { id: 'kimi-for-coding', label: 'Kimi For Coding', aliases: ['kimi-code', 'kimi-for-coding'] },
  ],
  zhipu: [
    { id: 'glm-5.1', label: 'GLM-5.1', aliases: ['glm5.1'] },
    { id: 'glm-5', label: 'GLM-5', aliases: ['glm', 'glm5'] },
    { id: 'glm-5-turbo', label: 'GLM-5 Turbo', aliases: ['glm5-turbo'] },
    { id: 'glm-4.7', label: 'GLM-4.7', aliases: ['glm4.7'] },
    { id: 'glm-4.5-air', label: 'GLM-4.5-Air', aliases: ['glm4.5-air'] },
  ],
  minimax: [
    { id: 'MiniMax-M2.7', label: 'MiniMax M2.7', aliases: ['minimax', 'm2.7'] },
    { id: 'MiniMax-M2.7-highspeed', label: 'MiniMax M2.7 Highspeed', aliases: ['minimax-fast'] },
    { id: 'MiniMax-M2.5', label: 'MiniMax M2.5', aliases: ['m2.5'] },
  ],
  mimo: [
    { id: 'mimo-v2.5-pro', label: 'MiMo V2.5 Pro', aliases: ['mimo', 'mimo-pro'], contextWindow: '1M', maxOutput: '128K' },
    { id: 'mimo-v2.5', label: 'MiMo V2.5', aliases: ['mimo-v2.5'], contextWindow: '1M', maxOutput: '128K' },
  ],
}

function fallbackModelsFor(providerId: string, ids: string[]): unknown[] {
  const models = FALLBACK_MODELS[providerId] || []
  return ids
    .map(id => models.find(model => model.id === id))
    .filter((model): model is (typeof models)[number] => !!model)
    .map((model, index) => ({
      id: model.id,
      label: model.label,
      upstreamName: providerId === 'zeelin' ? getZeeLinUpstreamModel(model.id) : model.id,
      enabled: true,
      sort: index + 1,
      aliases: model.aliases,
      contextWindow: model.contextWindow,
      maxOutput: model.maxOutput,
      capabilities: {
        maxContextTokens: model.contextWindow === '1M' ? 1_000_000 : undefined,
        maxOutputTokens: model.maxOutput === '128K' ? 131_072 : undefined,
      },
    }))
}

export const FALLBACK_PROVIDER_CATALOG = normalizeProviderCatalog({
  schemaVersion: 2,
  cacheTtlSeconds: 3600,
  providers: [
    {
      providerId: 'zeelin',
      providerName: 'Ziyor（智灵动力）',
      enabled: true,
      defaultBillingChannel: 'token',
      billingChannels: [
        { id: 'token', label: 'Token 通道', planName: 'Ziyor Token Plan', endpointKey: 'zeelin_token', models: fallbackModelsFor('zeelin', ['zeelin-deepseek-v4-pro', 'zeelin-qwen3.6-plus', 'zeelin-kimi-k2.6', 'zeelin-glm-5.1']) },
      ],
    },
    {
      providerId: 'openai',
      providerName: 'OpenAI',
      enabled: true,
      defaultBillingChannel: 'token',
      billingChannels: [
        { id: 'token', label: 'Token 通道', planName: 'OpenAI Token Plan', endpointKey: 'openai_token', models: fallbackModelsFor('openai', ['gpt-5.5', 'gpt-5.4', 'chat-latest']) },
      ],
    },
    {
      providerId: 'doubao',
      providerName: 'Doubao（豆包）',
      enabled: true,
      defaultBillingChannel: 'token',
      billingChannels: [
        { id: 'token', label: 'Token 通道', planName: 'Doubao Token Plan', endpointKey: 'doubao_token', models: fallbackModelsFor('doubao', ['doubao-seed-2.0-code', 'doubao-seed-2.0-pro', 'doubao-seed-2.0-lite']) },
        { id: 'coding', label: 'Coding 通道', planName: 'Doubao Coding Plan', endpointKey: 'doubao_coding', models: fallbackModelsFor('doubao', ['ark-code-latest']) },
      ],
    },
    {
      providerId: 'qwen',
      providerName: 'Qwen（通义千问）',
      enabled: true,
      defaultBillingChannel: 'token',
      billingChannels: [
        { id: 'token', label: 'Token 通道', planName: 'Qwen Token Plan', endpointKey: 'qwen_token', models: fallbackModelsFor('qwen', ['qwen3.7-max', 'qwen3.7-plus']) },
        { id: 'coding', label: 'Coding 通道', planName: 'Qwen Coding Plan', endpointKey: 'qwen_coding', models: fallbackModelsFor('qwen', ['qwen3.7-max', 'qwen3.6-plus', 'deepseek-v4-pro', 'deepseek-v4-flash']) },
      ],
    },
    {
      providerId: 'moonshot',
      providerName: 'Kimi（月之暗面）',
      enabled: true,
      defaultBillingChannel: 'token',
      billingChannels: [
        { id: 'token', label: 'Token 通道', planName: 'Kimi Token Plan', endpointKey: 'moonshot_token', models: fallbackModelsFor('moonshot', ['kimi-k2.6', 'kimi-k2.5']) },
        { id: 'coding', label: 'Coding 通道', planName: 'Kimi Code Plan', endpointKey: 'moonshot_coding', models: fallbackModelsFor('moonshot', ['kimi-for-coding']) },
      ],
    },
    {
      providerId: 'deepseek',
      providerName: 'DeepSeek（深度求索）',
      enabled: true,
      defaultBillingChannel: 'token',
      billingChannels: [
        { id: 'token', label: 'Token 通道', planName: 'DeepSeek Token Plan', endpointKey: 'deepseek_token', models: fallbackModelsFor('deepseek', ['deepseek-v4-pro', 'deepseek-v4-flash']) },
      ],
    },
    {
      providerId: 'zhipu',
      providerName: 'GLM（智谱AI）',
      enabled: true,
      defaultBillingChannel: 'token',
      billingChannels: [
        { id: 'token', label: 'Token 通道', planName: 'GLM Token Plan', endpointKey: 'zhipu_token', models: fallbackModelsFor('zhipu', ['glm-5.1', 'glm-5-turbo', 'glm-5']) },
        { id: 'coding', label: 'Coding 通道', planName: 'GLM Coding Plan', endpointKey: 'zhipu_coding', models: fallbackModelsFor('zhipu', ['glm-5.1', 'glm-5-turbo', 'glm-4.7', 'glm-4.5-air']) },
      ],
    },
    {
      providerId: 'minimax',
      providerName: 'MiniMax',
      enabled: true,
      defaultBillingChannel: 'token',
      billingChannels: [
        { id: 'token', label: 'Token 通道', planName: 'MiniMax Token Plan', endpointKey: 'minimax_token', models: fallbackModelsFor('minimax', ['MiniMax-M2.5', 'MiniMax-M2.7-highspeed']) },
        { id: 'coding', label: 'Coding 通道', planName: 'MiniMax Coding Plan', endpointKey: 'minimax_coding', models: fallbackModelsFor('minimax', ['MiniMax-M2.7']) },
      ],
    },
    {
      providerId: 'mimo',
      providerName: 'MiMo（小米）',
      enabled: true,
      defaultBillingChannel: 'token',
      billingChannels: [
        { id: 'token', label: 'Token 通道', planName: 'MiMo Token Plan', endpointKey: 'mimo_token', models: fallbackModelsFor('mimo', ['mimo-v2.5-pro', 'mimo-v2.5']) },
        {
          id: 'coding',
          label: 'Coding 通道',
          planName: 'MiMo Coding Plan',
          endpointKey: 'mimo_coding',
          models: [
            { id: 'MiMo-V2-Pro', label: 'MiMo V2 Pro', upstreamName: 'MiMo-V2-Pro', enabled: true, sort: 1, aliases: ['mimo-v2-pro'] },
            { id: 'MiMo-V2-Omni', label: 'MiMo V2 Omni', upstreamName: 'MiMo-V2-Omni', enabled: true, sort: 2, aliases: ['mimo-v2-omni'] },
          ],
        },
      ],
    },
  ],
})

let cachedProviderCatalog: (NormalizedProviderCatalog & { expiresAt: number }) | null = null

function applyProviderCatalog(catalog: NormalizedProviderCatalog): void {
  cachedProviderCatalog = {
    ...catalog,
    expiresAt: Date.now() + catalog.cacheTtlSeconds * 1000,
  }
}

function getCachedProviderCatalog(): NormalizedProviderCatalog | null {
  if (!cachedProviderCatalog || cachedProviderCatalog.expiresAt <= Date.now()) return null
  return cachedProviderCatalog
}

export function getProviderCatalogSnapshot(): NormalizedProviderCatalog {
  return getCachedProviderCatalog() || FALLBACK_PROVIDER_CATALOG
}

function readLocalProviderCatalog(): NormalizedProviderCatalog | null {
  if (process.env.NODE_ENV === 'test' && process.env[LOCAL_PROVIDER_CATALOG_TEST_FLAG] !== '1') return null

  const filePath = path.join(process.cwd(), LOCAL_PROVIDER_CATALOG_PATH)
  if (!fs.existsSync(filePath)) return null

  const raw = JSON.parse(fs.readFileSync(filePath, 'utf-8'))
  return normalizeProviderCatalog(raw)
}

export async function fetchProviderCatalog(options: {
  force?: boolean
  fallback?: NormalizedProviderCatalog
} = {}): Promise<NormalizedProviderCatalog> {
  try {
    const localCatalog = readLocalProviderCatalog()
    if (localCatalog) {
      applyProviderCatalog(localCatalog)
      return localCatalog
    }

    if (!options.force) {
      const cached = getCachedProviderCatalog()
      if (cached) return cached
    }

    const res = await fetch(ZEE_LIN_MODELS_V2_URL, {
      headers: { Accept: 'application/json' },
      cache: 'no-store',
      signal: AbortSignal.timeout(PROVIDER_CATALOG_FETCH_TIMEOUT_MS),
    })
    if (!res.ok) {
      const fallback = options.fallback || FALLBACK_PROVIDER_CATALOG
      applyProviderCatalog(fallback)
      return fallback
    }

    const catalog = normalizeProviderCatalog(await res.json())
    applyProviderCatalog(catalog)
    return catalog
  } catch {
    const fallback = options.fallback || FALLBACK_PROVIDER_CATALOG
    applyProviderCatalog(fallback)
    return fallback
  }
}

export function findCatalogModel(catalog: NormalizedProviderCatalog, modelId: string): CatalogModel | null {
  for (const provider of catalog.providers) {
    for (const channel of provider.billingChannels) {
      const model = channel.models.find(item => item.id === modelId)
      if (model) return model
    }
  }

  return null
}
