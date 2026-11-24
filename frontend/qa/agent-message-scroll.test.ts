import test from 'node:test';
import assert from 'node:assert/strict';
import * as helpers from '../src/lib/agentMessageScroll.ts';
const setup = () => {
  assert.equal(typeof helpers.createAgentMessageScroll, 'function', 'agent scrolling behavior must be implemented');
  const viewport = { scrollTop: 0, scrollHeight: 2200, clientHeight: 500 };
  const scroll = helpers.createAgentMessageScroll(() => viewport);
  const show = (session = 'existing', ready = true, active = true) => scroll.update({ session, ready, active });
  return { viewport, scroll, show };
};

test('a restored conversation opens at its latest message after loading', () => {
  const { viewport, show } = setup();
  show('existing', false);
  assert.equal(viewport.scrollTop, 0);
  show();
  assert.equal(viewport.scrollTop, 1700);
});

test('new content and a shorter viewport follow when the reader was at the bottom', () => {
  const { viewport, show } = setup();
  show();
  viewport.scrollHeight += 700;
  show();
  assert.equal(viewport.scrollTop, 2400);
  viewport.clientHeight = 350;
  show();
  assert.equal(viewport.scrollTop, 2550);
});

test('reading history survives replies, evidence expansion and a hide/show cycle', () => {
  const { viewport, scroll, show } = setup();
  show();
  viewport.scrollTop = 340;
  scroll.onScroll();
  viewport.scrollHeight += 800;
  show();
  assert.equal(viewport.scrollTop, 340);
  assert.equal(scroll.showLatest, true);
  show('existing', true, false);
  viewport.scrollTop = 0;
  scroll.onScroll(); // Hidden elements must not overwrite the saved reading position.
  show();
  assert.equal(viewport.scrollTop, 340);
});

test('sending or clicking latest follows immediately but later manual scrolling wins', () => {
  const { viewport, scroll, show } = setup();
  show();
  viewport.scrollTop = 100;
  scroll.onScroll();
  scroll.followLatest();
  assert.equal(viewport.scrollTop, 1700);
  assert.equal(scroll.showLatest, false);
  viewport.scrollTop = 300;
  scroll.onScroll();
  viewport.scrollHeight += 800;
  show();
  assert.equal(viewport.scrollTop, 300);
});

test('switching or starting a conversation resets stale scroll and latest indicators', () => {
  const { viewport, scroll, show } = setup();
  show();
  viewport.scrollTop = 250;
  scroll.onScroll();
  viewport.scrollHeight = 500;
  show('new');
  assert.equal(viewport.scrollTop, 0);
  assert.equal(scroll.showLatest, false);
  viewport.scrollHeight = 3500;
  show('other');
  assert.equal(viewport.scrollTop, 3000);
});

test('near-bottom scroll tolerance permits follow without fractional pixel jitter', () => {
  const { viewport, scroll, show } = setup();
  show();
  viewport.scrollTop = 1699.25;
  scroll.onScroll();
  viewport.scrollHeight += 100;
  show();
  assert.equal(viewport.scrollTop, 1800);
  assert.equal(scroll.showLatest, false);
});

test('Enter sends once while Shift+Enter, repeats and Chinese composition do not send', () => {
  assert.equal(typeof helpers.shouldSendAgentMessage, 'function', 'composer keyboard behavior must be implemented');
  const enter = { key: 'Enter', shiftKey: false, repeat: false, isComposing: false, keyCode: 13 };
  assert.equal(helpers.shouldSendAgentMessage(enter), true);
  assert.equal(helpers.shouldSendAgentMessage({ ...enter, shiftKey: true }), false);
  assert.equal(helpers.shouldSendAgentMessage({ ...enter, repeat: true }), false);
  assert.equal(helpers.shouldSendAgentMessage({ ...enter, isComposing: true }), false);
  assert.equal(helpers.shouldSendAgentMessage({ ...enter, keyCode: 229 }), false);
  assert.equal(helpers.shouldSendAgentMessage(enter, true), false);
  assert.equal(helpers.shouldSendAgentMessage({ ...enter, key: 'a' }), false);
});
