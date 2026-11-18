import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  AppState,
  CreateWorkflowInput,
  Policy,
  Workflow,
  WorkflowAction,
} from "../types.ts";
import { createGateway } from "./gateway.ts";
import type { MockWorkflowGateway, WorkflowGateway } from "./gateway.ts";
import type { WorkspaceImportResult } from "./workspaceTransfer.ts";

export interface WorkflowStore {
  state: AppState;
  mode: WorkflowGateway["mode"];
  error: string | null;
  create(input: CreateWorkflowInput): Promise<Workflow>;
  act(id: string, action: WorkflowAction): Promise<Workflow>;
  policy(id: string, policy: Policy): Promise<Workflow>;
  resetDemo(): void;
  advance(seconds: number): void;
  canImport: boolean;
  importWorkflows(json: string): Promise<WorkspaceImportResult>;
}

function isMockGateway(
  gateway: WorkflowGateway,
): gateway is MockWorkflowGateway {
  return (
    gateway.mode === "mock" &&
    "snapshot" in gateway &&
    "advance" in gateway &&
    "resetDemo" in gateway
  );
}

function initialState(gateway: WorkflowGateway): AppState {
  return isMockGateway(gateway)
    ? gateway.snapshot()
    : { version: 1, workflows: [] };
}

function message(error: unknown): string {
  return error instanceof Error
    ? error.message
    : "工作流数据操作失败，请稍后重试。";
}

/** The UI owns no workflow mutations: all data and commands pass through this adapter. */
export function useWorkflowStore(injected?: WorkflowGateway): WorkflowStore {
  const gateway = useMemo(() => createGateway(injected), [injected]);
  const gatewayRef = useRef(gateway);
  const revisionRef = useRef(0);
  gatewayRef.current = gateway;
  const [state, setState] = useState<AppState>(() => initialState(gateway));
  const stateRef = useRef(state);
  const [error, setError] = useState<string | null>(null);
  const acceptSnapshot = useCallback((snapshot: AppState) => {
    revisionRef.current += 1;
    stateRef.current = snapshot;
    setState(snapshot);
  }, []);

  useEffect(() => {
    let disposed = false;
    let unsubscribe = () => {};
    acceptSnapshot(initialState(gateway));
    setError(null);
    try {
      unsubscribe = gateway.subscribe((snapshot) => {
        if (disposed) return;
        acceptSnapshot(snapshot);
        setError(null);
      });
      const requestedRevision = revisionRef.current;
      void gateway
        .list()
        .then((workflows) => {
          // A newer subscription snapshot wins over a slower initial list response.
          if (!disposed && revisionRef.current === requestedRevision)
            acceptSnapshot({ version: 1, workflows });
        })
        .catch((cause) => {
          if (!disposed && revisionRef.current === requestedRevision)
            setError(message(cause));
        });
    } catch (cause) {
      setError(message(cause));
    }
    return () => {
      disposed = true;
      unsubscribe();
    };
  }, [gateway, acceptSnapshot]);

  const acceptResult = useCallback(
    (workflow: Workflow, requestedRevision: number) => {
      // Subscribed snapshots are authoritative; slow command responses cannot rewind them.
      if (
        gatewayRef.current !== gateway ||
        revisionRef.current !== requestedRevision
      )
        return false;
      const current = stateRef.current;
      acceptSnapshot({
        version: 1,
        workflows: current.workflows.some((item) => item.id === workflow.id)
          ? current.workflows.map((item) =>
              item.id === workflow.id ? workflow : item,
            )
          : [workflow, ...current.workflows],
      });
      setError(null);
      return true;
    },
    [gateway, acceptSnapshot],
  );

  const create = useCallback(
    async (input: CreateWorkflowInput) => {
      const requestedRevision = revisionRef.current;
      try {
        const workflow = await gateway.create(input);
        const accepted = acceptResult(workflow, requestedRevision);
        if (
          !accepted &&
          gatewayRef.current === gateway &&
          !stateRef.current.workflows.some((item) => item.id === workflow.id)
        ) {
          // A newer authoritative snapshot can be unrelated to this create.
          // Refresh instead of merging an older response over that snapshot.
          const refreshRevision = revisionRef.current;
          try {
            const workflows = await gateway.list();
            if (
              gatewayRef.current === gateway &&
              revisionRef.current === refreshRevision
            ) {
              acceptSnapshot({ version: 1, workflows });
              setError(null);
            }
          } catch (cause) {
            if (
              gatewayRef.current === gateway &&
              revisionRef.current === refreshRevision
            )
              setError(`工作流已创建，但列表刷新失败：${message(cause)}`);
          }
        }
        return workflow;
      } catch (cause) {
        if (
          gatewayRef.current === gateway &&
          revisionRef.current === requestedRevision
        )
          setError(message(cause));
        throw cause;
      }
    },
    [gateway, acceptResult, acceptSnapshot],
  );

  const act = useCallback(
    async (id: string, action: WorkflowAction) => {
      const requestedRevision = revisionRef.current;
      try {
        const workflow = await gateway.act(id, action);
        acceptResult(workflow, requestedRevision);
        return workflow;
      } catch (cause) {
        if (
          gatewayRef.current === gateway &&
          revisionRef.current === requestedRevision
        )
          setError(message(cause));
        throw cause;
      }
    },
    [gateway, acceptResult],
  );

  const policy = useCallback(
    async (id: string, policy: Policy) => {
      const requestedRevision = revisionRef.current;
      try {
        const workflow = await gateway.policy(id, policy);
        acceptResult(workflow, requestedRevision);
        return workflow;
      } catch (cause) {
        if (
          gatewayRef.current === gateway &&
          revisionRef.current === requestedRevision
        )
          setError(message(cause));
        throw cause;
      }
    },
    [gateway, acceptResult],
  );

  const resetDemo = useCallback(() => {
    if (!isMockGateway(gateway))
      throw new Error("真实后端模式不支持重置演示数据。");
    gateway.resetDemo();
    acceptSnapshot(gateway.snapshot());
    setError(null);
  }, [gateway, acceptSnapshot]);

  const importWorkflows = useCallback(
    async (json: string) => {
      if (!gateway.importWorkflows)
        throw new Error(
          "当前后端适配器未提供工作区导入接口，此模式下不能导入文件。",
        );
      const requestedRevision = revisionRef.current;
      try {
        const result = await gateway.importWorkflows(json);
        if (gatewayRef.current !== gateway) return result;
        const needsRefresh =
          revisionRef.current === requestedRevision ||
          result.importedIds.some(
            (id) =>
              !stateRef.current.workflows.some(
                (workflow) => workflow.id === id,
              ),
          );
        if (needsRefresh) {
          const refreshRevision = revisionRef.current;
          try {
            const workflows = await gateway.list();
            if (
              gatewayRef.current === gateway &&
              revisionRef.current === refreshRevision
            ) {
              acceptSnapshot({ version: 1, workflows });
              setError(null);
            }
          } catch (cause) {
            if (
              gatewayRef.current === gateway &&
              revisionRef.current === refreshRevision
            )
              setError(`导入已完成，但列表刷新失败：${message(cause)}`);
          }
        }
        return result;
      } catch (cause) {
        if (
          gatewayRef.current === gateway &&
          revisionRef.current === requestedRevision
        )
          setError(message(cause));
        throw cause;
      }
    },
    [gateway, acceptSnapshot],
  );

  const advance = useCallback(
    (seconds: number) => {
      if (isMockGateway(gateway)) {
        try {
          gateway.advance(seconds);
        } catch (cause) {
          if (gatewayRef.current === gateway) setError(message(cause));
        }
      }
    },
    [gateway],
  );

  return {
    state,
    mode: gateway.mode,
    error,
    create,
    act,
    policy,
    resetDemo,
    advance,
    canImport: typeof gateway.importWorkflows === "function",
    importWorkflows,
  };
}
