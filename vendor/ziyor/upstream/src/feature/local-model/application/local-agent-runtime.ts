import crypto from 'node:crypto'
import { LOCAL_AGENT_MAX_JSON_REPAIR_ATTEMPTS, LOCAL_AGENT_MAX_TOOL_ROUNDS } from '../domain/local-agent-limits'
import { parseLocalAgentAction } from '../domain/local-agent-protocol'
import { validateLocalAgentToolArguments } from '../domain/local-agent-tool-validation'
import type {
  LocalAgentPermissionMode,
  LocalAgentTool,
  LocalAgentToolCall,
  LocalAgentToolContext,
} from '../domain/local-agent-tool'
import { formatToolDefinitionsForPrompt } from '../domain/local-agent-tool'
import { createBuiltinLocalAgentTools, createLocalAgentToolMap } from '../infra/local-agent-tools'
import type { LocalAgentSkillContext } from '../infra/local-agent-skill-loader'
import { completeLocalChatOnce, type LocalChatMessage } from './local-chat'
import { localAgentToolResultEvent, localAgentToolUseEvent } from './local-agent-sse'

export interface RunLocalAgentOptions {
  baseUrl: string
  model: string
  workspacePath: string
  permissionMode: LocalAgentPermissionMode
  messages: LocalChatMessage[]
  requestedSkillNames?: string[]
  /** Guardian/SQLite-prepared context. Production callers may not fall back
   * to project filesystem reads inside the local runtime. */
  skillContext: LocalAgentSkillContext
  /** Trusted, frozen project governance contract. It never changes tool authority. */
  systemPromptAppend?: string
  tools?: LocalAgentTool[]
  promptTools?: LocalAgentTool[]
  maxToolRounds?: number
  signal?: AbortSignal
  requestPermission?: LocalAgentToolContext['requestPermission']
  complete?: typeof completeLocalChatOnce
  emit?: (event: ReturnType<typeof localAgentToolUseEvent> | ReturnType<typeof localAgentToolResultEvent>) => void | Promise<void>
}

export interface RunLocalAgentResult {
  content: string
}

function buildSystemPrompt(options: RunLocalAgentOptions, tools: LocalAgentTool[]): string {
  const skillContext = options.skillContext

  const parts = [
    '你是 Ziyor 本地 Agent。你运行在用户本机，不走云端。',
    '你必须只输出 JSON，不要输出 Markdown，不要输出解释或前后缀文本。',
    '需要工具时输出 {"type":"tool_call","tool":"工具名","arguments":{...}}。',
    '完成任务时输出 {"type":"final","content":"最终回答"}。',
    `workspacePath: ${options.workspacePath}`,
    `permissionMode: ${options.permissionMode}`,
    `可用工具 JSON:\n${formatToolDefinitionsForPrompt(tools.map(tool => tool.definition))}`,
  ]

  if (skillContext.content) {
    parts.push(`已加载 skill context:\n${skillContext.content}`)
  }

  if (options.systemPromptAppend) {
    parts.push(options.systemPromptAppend)
  }

  return parts.join('\n\n')
}

function observationMessage(result: { ok: boolean; content: string }): LocalChatMessage {
  return {
    role: 'user',
    content: `工具执行结果：\n${JSON.stringify(result)}`,
  }
}

function toolExecutionErrorMessage(error: unknown): string {
  if (error instanceof Error && error.message) {
    return error.message
  }
  return '工具执行失败'
}

async function emitEvent(
  emit: RunLocalAgentOptions['emit'],
  event: ReturnType<typeof localAgentToolUseEvent> | ReturnType<typeof localAgentToolResultEvent>,
) {
  if (!emit) return
  await emit(event)
}

export async function runLocalAgent(options: RunLocalAgentOptions): Promise<RunLocalAgentResult> {
  const complete = options.complete ?? completeLocalChatOnce
  const tools = options.tools ?? createBuiltinLocalAgentTools()
  const promptTools = options.promptTools ?? tools
  const toolMap = createLocalAgentToolMap(tools)
  const maxToolRounds = options.maxToolRounds ?? LOCAL_AGENT_MAX_TOOL_ROUNDS
  const modelMessages: LocalChatMessage[] = [
    { role: 'system', content: buildSystemPrompt(options, promptTools) },
    ...options.messages,
  ]
  let repairAttempts = 0
  let toolCallCount = 0

  while (true) {
    const raw = await complete({
      baseUrl: options.baseUrl,
      model: options.model,
      messages: modelMessages,
      signal: options.signal,
    })

    const parsed = parseLocalAgentAction(raw)
    if (!parsed.ok) {
      if (repairAttempts < LOCAL_AGENT_MAX_JSON_REPAIR_ATTEMPTS) {
        repairAttempts += 1
        modelMessages.push({
          role: 'user',
          content: parsed.repairPrompt,
        })
        continue
      }

      throw new Error(parsed.error)
    }

    repairAttempts = 0

    if (parsed.action.type === 'final') {
      return { content: parsed.action.content }
    }

    if (toolCallCount >= maxToolRounds) {
      throw new Error('超过最大工具轮数，本地 Agent 已停止。')
    }
    toolCallCount += 1

    const call: LocalAgentToolCall = {
      id: crypto.randomUUID(),
      name: parsed.action.tool,
      arguments: parsed.action.arguments,
    }
    const tool = toolMap.get(parsed.action.tool)

    if (!tool) {
      const result = {
        toolCallId: call.id,
        ok: false,
        content: `未知工具：${parsed.action.tool}`,
      }
      await emitEvent(options.emit, localAgentToolResultEvent(result))
      modelMessages.push(observationMessage({ ok: result.ok, content: result.content }))
      continue
    }

    await emitEvent(options.emit, localAgentToolUseEvent(call, tool.definition))
    const validation = validateLocalAgentToolArguments(call, tool.definition)
    if (!validation.ok) {
      const result = {
        toolCallId: call.id,
        ok: false,
        content: validation.error,
      }
      await emitEvent(options.emit, localAgentToolResultEvent(result))
      modelMessages.push(observationMessage({ ok: result.ok, content: result.content }))
      continue
    }

    let result
    try {
      result = await tool.execute(call, {
        workspacePath: options.workspacePath,
        permissionMode: options.permissionMode,
        signal: options.signal,
        requestPermission: options.requestPermission,
      })
    } catch (error) {
      result = {
        toolCallId: call.id,
        ok: false,
        content: toolExecutionErrorMessage(error),
      }
    }
    await emitEvent(options.emit, localAgentToolResultEvent(result))
    modelMessages.push(observationMessage({ ok: result.ok, content: result.content }))
  }
}
