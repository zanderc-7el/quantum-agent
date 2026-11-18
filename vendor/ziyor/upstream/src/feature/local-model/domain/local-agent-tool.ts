export type LocalAgentPermissionKind = 'none' | 'write' | 'command' | 'network'
export type LocalAgentPermissionMode = 'confirm' | 'full' | (string & {})

export interface LocalAgentToolDefinition {
  name: string
  description: string
  permission: LocalAgentPermissionKind
  inputSchema: Record<string, unknown>
}

export interface LocalAgentToolCall {
  id: string
  name: string
  arguments: Record<string, unknown>
}

export interface LocalAgentToolResult {
  toolCallId: string
  ok: boolean
  content: string
  metadata?: Record<string, unknown>
}

export interface LocalAgentToolContext {
  workspacePath: string
  permissionMode: LocalAgentPermissionMode
  signal?: AbortSignal
  requestPermission?: (
    call: LocalAgentToolCall,
    definition: LocalAgentToolDefinition,
  ) => Promise<boolean>
}

export interface LocalAgentTool {
  definition: LocalAgentToolDefinition
  execute(
    call: LocalAgentToolCall,
    context: LocalAgentToolContext,
  ): Promise<LocalAgentToolResult>
}

export function formatToolDefinitionsForPrompt(definitions: LocalAgentToolDefinition[]): string {
  return JSON.stringify(definitions.map(definition => ({
    name: definition.name,
    description: definition.description,
    permission: definition.permission,
    input_schema: definition.inputSchema,
  })), null, 2)
}
