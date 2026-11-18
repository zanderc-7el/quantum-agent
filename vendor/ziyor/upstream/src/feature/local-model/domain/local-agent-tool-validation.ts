import type { LocalAgentToolCall, LocalAgentToolDefinition } from './local-agent-tool'

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
}

function validateType(value: unknown, expectedType: string) {
  if (expectedType === 'array') return Array.isArray(value)
  if (expectedType === 'object') return Boolean(value && typeof value === 'object' && !Array.isArray(value))
  return typeof value === expectedType
}

export function validateLocalAgentToolArguments(
  call: LocalAgentToolCall,
  definition: LocalAgentToolDefinition,
): { ok: true } | { ok: false; error: string } {
  const schema = asRecord(definition.inputSchema)
  if (!schema) return { ok: true }

  const required = Array.isArray(schema.required)
    ? schema.required.filter(item => typeof item === 'string')
    : []
  for (const field of required) {
    if (!(field in call.arguments)) {
      return { ok: false, error: `${field} is required` }
    }
  }

  const properties = asRecord(schema.properties)
  if (!properties) return { ok: true }

  for (const [field, propertySchema] of Object.entries(properties)) {
    if (!(field in call.arguments)) continue
    const property = asRecord(propertySchema)
    const expectedType = typeof property?.type === 'string' ? property.type : ''
    if (expectedType && !validateType(call.arguments[field], expectedType)) {
      return { ok: false, error: `${field} must be ${expectedType}` }
    }
  }

  return { ok: true }
}
