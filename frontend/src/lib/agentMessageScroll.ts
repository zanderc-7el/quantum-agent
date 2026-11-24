interface ScrollViewport { scrollTop: number; scrollHeight: number; clientHeight: number }
export interface AgentScrollContext { session: string; ready: boolean; active: boolean }

/** Track the reader's intent before content changes, never infer it from the new height. */
export function createAgentMessageScroll(viewport: () => ScrollViewport | null) {
  let session = '';
  let active = false;
  let ready = false;
  let initialized = false;
  let following = true;
  let readingTop = 0;
  const bottom = (element: ScrollViewport) => Math.max(0, element.scrollHeight - element.clientHeight);
  const followLatest = () => {
    following = true;
    const element = viewport();
    if (element && active && ready) element.scrollTop = readingTop = bottom(element);
  };
  return {
    get showLatest() { return !following; },
    followLatest,
    onScroll() {
      const element = viewport();
      if (!element || !active || !ready || !initialized) return;
      readingTop = element.scrollTop;
      following = bottom(element) - readingTop <= 48;
    },
    update(context: AgentScrollContext) {
      if (session !== context.session) {
        session = context.session;
        following = true;
        initialized = false;
        readingTop = 0;
      }
      active = context.active;
      ready = context.ready;
      const element = viewport();
      if (!element || !active || !ready) return;
      if (!initialized || following) followLatest();
      else element.scrollTop = Math.min(readingTop, bottom(element));
      initialized = true;
    },
  };
}

export function shouldSendAgentMessage(
  event: { key: string; shiftKey: boolean; repeat?: boolean; isComposing?: boolean; keyCode?: number },
  composing = false,
) {
  return event.key === 'Enter' && !event.shiftKey && !event.repeat
    && !event.isComposing && !composing && event.keyCode !== 229;
}
