export type LocalAgentAction =
  | { type: 'tool_call'; tool: string; arguments: Record<string, unknown> }
  | { type: 'final'; content: string }

export type LocalAgentActionParseResult =
  | { ok: true; action: LocalAgentAction }
  | { ok: false; error: string; repairPrompt: string }

const REPAIR_PROMPT = [
  '你的上一条输出不是合法的本地 Agent JSON。',
  '请只输出 JSON，不要输出 Markdown、解释、前后缀文本。',
  '合法格式只能是：',
  '{"type":"tool_call","tool":"read_file","arguments":{"path":"README.md"}}',
  '或：',
  '{"type":"final","content":"最终回答"}',
].join('\n')

function stripJsonFence(raw: string): string {
  const value = raw.trim()
  const fenced = value.match(/^```(?:json)?\s*([\s\S]*?)\s*```$/i)
  return (fenced?.[1] || value).trim()
}

function extractFirstJsonObject(raw: string): string | null {
  const value = stripJsonFence(raw)
  const start = value.indexOf('{')
  if (start < 0) return null

  let depth = 0
  let inString = false
  let escaping = false

  for (let index = start; index < value.length; index += 1) {
    const char = value[index]

    if (inString) {
      if (escaping) {
        escaping = false
      } else if (char === '\\') {
        escaping = true
      } else if (char === '"') {
        inString = false
      }
      continue
    }

    if (char === '"') {
      inString = true
      continue
    }

    if (char === '{') depth += 1
    if (char === '}') {
      depth -= 1
      if (depth === 0) return value.slice(start, index + 1)
    }
  }

  return null
}

function failure(error: string): LocalAgentActionParseResult {
  return { ok: false, error, repairPrompt: REPAIR_PROMPT }
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
}

function hasOnlyFields(record: Record<string, unknown>, allowedFields: string[]): boolean {
  return Object.keys(record).every((field) => allowedFields.includes(field))
}

export function parseLocalAgentAction(raw: string): LocalAgentActionParseResult {
  let parsed: unknown
  try {
    parsed = JSON.parse(stripJsonFence(raw))
  } catch {
    const extracted = extractFirstJsonObject(raw)
    if (!extracted) return failure('模型输出不是合法 JSON')
    try {
      parsed = JSON.parse(extracted)
    } catch {
      return failure('模型输出不是合法 JSON')
    }
  }

  const record = asRecord(parsed)
  if (!record) return failure('模型输出 JSON 必须是对象')

  if (record.type === 'final') {
    if (!hasOnlyFields(record, ['type', 'content'])) return failure('final 包含 schema 外字段')
    if (typeof record.content !== 'string') return failure('final.content 必须是字符串')
    return { ok: true, action: { type: 'final', content: record.content } }
  }

  if (record.type === 'tool_call') {
    if (!hasOnlyFields(record, ['type', 'tool', 'arguments'])) return failure('tool_call 包含 schema 外字段')
    if (typeof record.tool !== 'string' || !record.tool.trim()) return failure('tool_call.tool 必须是非空字符串')
    const args = record.arguments === undefined ? {} : asRecord(record.arguments)
    if (!args) return failure('tool_call.arguments 必须是对象')
    return { ok: true, action: { type: 'tool_call', tool: record.tool.trim(), arguments: args } }
  }

  return failure('type 必须是 tool_call 或 final')
}
