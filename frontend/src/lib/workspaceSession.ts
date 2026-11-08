/** Demo product access applies only to an explicitly injected mock gateway.
 * Remote and standalone workspaces always retain their normal session check. */
export function hasWorkspaceSession(productMode: boolean, injectedMode: "mock" | "remote" | undefined, sessionId: string | undefined, userId: string): boolean {
  return (productMode && injectedMode === "mock") || sessionId === userId;
}
