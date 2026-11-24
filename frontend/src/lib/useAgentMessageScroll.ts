import { useLayoutEffect, useRef, useState } from 'react';
import { createAgentMessageScroll } from './agentMessageScroll';
import type { AgentScrollContext } from './agentMessageScroll';

export function useAgentMessageScroll(context: AgentScrollContext, revision: unknown) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const contextRef = useRef(context);
  contextRef.current = context;
  const controllerRef = useRef<ReturnType<typeof createAgentMessageScroll> | null>(null);
  if (!controllerRef.current) controllerRef.current = createAgentMessageScroll(() => viewportRef.current);
  const controller = controllerRef.current;
  const [showLatest, setShowLatest] = useState(false);

  useLayoutEffect(() => {
    controller.update(context);
    setShowLatest(controller.showLatest);
  }, [context.session, context.active, context.ready, revision, controller]);

  useLayoutEffect(() => {
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(() => {
      controller.update(contextRef.current);
      setShowLatest(controller.showLatest);
    });
    if (viewportRef.current) observer.observe(viewportRef.current);
    if (contentRef.current) observer.observe(contentRef.current);
    return () => observer.disconnect();
  }, [controller]);

  return {
    viewportRef, contentRef, showLatest,
    onScroll: () => { controller.onScroll(); setShowLatest(controller.showLatest); },
    followLatest: () => { controller.followLatest(); setShowLatest(controller.showLatest); },
  };
}
