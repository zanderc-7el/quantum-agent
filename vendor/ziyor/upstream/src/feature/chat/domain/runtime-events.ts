import type { TurnEvidenceTrace } from './turn-evidence'

export type ChatRuntime = 'claude-sdk' | 'codex' | 'claude-cli' | 'local-model' | 'full-ai'

export type ChatRuntimeScope =
  | { kind: 'main' }
  | { kind: 'agent'; parentToolUseId: string }
  | { kind: 'full-ai-group' }
  | { kind: 'full-ai-model'; modelId: string }
  | { kind: 'full-ai-synthesis'; modelId?: string }

export interface RuntimeTokenUsage {
  inputTokens?: number
  outputTokens?: number
  cachedInputTokens?: number
  reasoningOutputTokens?: number
  rawUsageJson?: string
}

export type RuntimeReconnectState = 'waiting' | 'attempting' | 'recovered'

export type RuntimePermissionDecision = 'allow' | 'allow_session' | 'deny' | 'timeout'

export type RuntimeAgentContentEvent =
  | { type: 'runtime_agent_content'; runtime: ChatRuntime; parentToolUseId: string; blockType: 'text' | 'text_delta' | 'thinking' | 'thinking_delta'; text: string }
  | { type: 'runtime_agent_content'; runtime: ChatRuntime; parentToolUseId: string; blockType: 'tool_use'; id: string; name: string; input: Record<string, unknown> }
  | { type: 'runtime_agent_content'; runtime: ChatRuntime; parentToolUseId: string; blockType: 'tool_result'; toolUseId: string; content: string; isError: boolean }
  | { type: 'runtime_agent_content'; runtime: ChatRuntime; parentToolUseId: string; blockType: 'tool_progress'; toolUseId: string; toolName: string; elapsedSeconds: number }

export type RuntimeTaskEventType =
  | 'task_started'
  | 'task_updated'
  | 'task_progress'
  | 'task_completed'
  | 'task_failed'

type RuntimeTaskEventBase = {
  type: 'runtime_task_event'
  runtime: ChatRuntime
  taskId: string
  title?: string
  status?: string
  summary?: string
  elapsedSeconds?: number
  parentToolUseId?: string
  error?: string
  raw: Record<string, unknown>
}

export type RuntimeTaskEvent =
  | (RuntimeTaskEventBase & { taskEventType: 'task_started'; title: string })
  | (RuntimeTaskEventBase & { taskEventType: 'task_failed'; error: string })
  | (RuntimeTaskEventBase & { taskEventType: Exclude<RuntimeTaskEventType, 'task_started' | 'task_failed'> })

export type RuntimeFullAiModelStatus = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'

export type RuntimeFullAiSynthesisStatus = 'pending' | 'running' | 'completed' | 'failed' | 'skipped'

export interface RuntimeFullAiModelResult {
  modelId: string
  label: string
  provider: string
  status: RuntimeFullAiModelStatus
  text: string
  thinking?: string
  inputTokens?: number
  outputTokens?: number
  elapsedSeconds?: number
  error?: string
}

export interface RuntimeFullAiSynthesisResult {
  enabled: boolean
  status: RuntimeFullAiSynthesisStatus
  text: string
  taskType?: string
  strategy?: string
  error?: string
}

export type ChatRuntimeEvent =
  | { type: 'runtime_text_delta'; runtime: ChatRuntime; scope?: ChatRuntimeScope; text: string }
  | { type: 'runtime_thinking_start'; runtime: ChatRuntime; scope?: ChatRuntimeScope }
  | { type: 'runtime_thinking_delta'; runtime: ChatRuntime; scope?: ChatRuntimeScope; text: string }
  | { type: 'runtime_turn_trace_update'; runtime: ChatRuntime; trace: Partial<TurnEvidenceTrace> }
  | { type: 'runtime_turn_trace_done'; runtime: ChatRuntime; trace: TurnEvidenceTrace }
  | { type: 'runtime_tool_use'; runtime: ChatRuntime; scope?: ChatRuntimeScope; id: string; name: string; input: Record<string, unknown>; permission?: string }
  | { type: 'runtime_tool_result'; runtime: ChatRuntime; scope?: ChatRuntimeScope; toolUseId: string; name?: string; content: string; isError: boolean }
  | { type: 'runtime_tool_raw_result'; runtime: ChatRuntime; scope?: ChatRuntimeScope; toolUseId: string; toolName: string; rawContent: Record<string, unknown> }
  | { type: 'runtime_tool_progress'; runtime: ChatRuntime; scope?: ChatRuntimeScope; toolUseId: string; toolName: string; elapsedSeconds: number }
  | RuntimeAgentContentEvent
  | RuntimeTaskEvent
  | { type: 'runtime_permission_request'; runtime: ChatRuntime; requestId: string; toolName: string; toolInput: Record<string, unknown>; toolUseId?: string; privacyRisk?: 'low' | 'medium' | 'high'; privacyReason?: string; privacyBehavior?: 'allow' | 'redact' | 'confirm' | 'deny' }
  | { type: 'runtime_permission_resolved'; runtime: ChatRuntime; requestId: string; decision: RuntimePermissionDecision }
  | ({ type: 'runtime_usage'; runtime: ChatRuntime; scope?: ChatRuntimeScope } & RuntimeTokenUsage)
  | { type: 'runtime_reconnect_status'; runtime: 'codex'; attempt: number; maxAttempts: number; retryInMs: number; state: RuntimeReconnectState }
  | { type: 'runtime_done'; runtime: ChatRuntime; scope?: ChatRuntimeScope; messageId?: string; userMessageId?: string; inputTokens?: number; outputTokens?: number; models?: RuntimeFullAiModelResult[]; synthesis?: RuntimeFullAiSynthesisResult; taskType?: string; strategy?: string }
  | { type: 'runtime_error'; runtime: ChatRuntime; scope?: ChatRuntimeScope; error: string; modelId?: string }
  | { type: 'runtime_attachment'; runtime: ChatRuntime; attachment: Record<string, unknown> }
  | { type: 'runtime_full_ai_group_started'; runtime: 'full-ai'; sessionId: string; userMessageId: string; modelIds: string[]; taskType?: string; strategy?: string }
  | { type: 'runtime_full_ai_model_started'; runtime: 'full-ai'; modelId: string; label: string; provider: string }
  | { type: 'runtime_full_ai_model_done'; runtime: 'full-ai'; modelId: string; result: RuntimeFullAiModelResult }
  | { type: 'runtime_full_ai_model_error'; runtime: 'full-ai'; modelId: string; error: string }
  | { type: 'runtime_full_ai_synthesis_started'; runtime: 'full-ai' }
  | { type: 'runtime_full_ai_synthesis_done'; runtime: 'full-ai'; text: string }
  | { type: 'runtime_full_ai_synthesis_error'; runtime: 'full-ai'; error: string }

export function isRuntimeTaskEvent(event: ChatRuntimeEvent): event is Extract<ChatRuntimeEvent, { type: 'runtime_task_event' }> {
  return event.type === 'runtime_task_event'
}

export function isRuntimeUsageEvent(event: ChatRuntimeEvent): event is Extract<ChatRuntimeEvent, { type: 'runtime_usage' }> {
  return event.type === 'runtime_usage'
}
