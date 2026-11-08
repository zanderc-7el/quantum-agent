import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
const html = readFileSync(new URL("../../prototype/quantamind-product.html", import.meta.url), "utf8");
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
function shell(hash = "#agent", protocol = "http:") {
  const events = {}, documentEvents = {}, nodes = new Map(), messages = [], historyEntries = [];
  const location = { hash, protocol, origin: "http://localhost:5286", href: "http://localhost:5286/product.html" + hash };
  const node = selector => {
    if (!nodes.has(selector)) nodes.set(selector, { hidden:false, innerHTML:"", textContent:"", href:"", src:"", classList:{add(){},remove(){}}, addEventListener(name,fn){this[name]=fn;}, contentWindow: { postMessage(...args){messages.push(args);} } });
    return nodes.get(selector);
  };
  const window = { addEventListener(name,fn){events[name]=fn;}, dispatchEvent(event){messages.push(event);events[event.type]?.(event);}, scrollTo(){} };
  const history = { pushState(_a,_b,url){historyEntries.push(url);location.hash=url;} };
  vm.runInNewContext(script, {CustomEvent: class {constructor(type, options){this.type=type;this.detail=options.detail;}},window,document:{querySelector:node,addEventListener(name,fn){(documentEvents[name]??=[]).push(fn);}},location,history,URL,Intl,Date,Math,JSON,Number,String,Object,Array,localStorage:{getItem(){return null;}},setTimeout(){},clearTimeout(){},setInterval(){},clearInterval(){} });
  const click = id => { const target={dataset:{action:"nav",id}};documentEvents.click.forEach(fn=>fn({target:{closest(selector){return selector==='[data-action]'?target:null;}}})); };
  return {node,events,location,click,messages,historyEntries};
}
test("original two product entries are replaced without iframe, duplicate navigation or login",()=>{
  assert.doesNotMatch(html, /<iframe|workspace-frame|工作空间 · 共享任务|workspace-dashboard/);
  const s=shell();assert.equal(s.node('#product-module').hidden,false);
  assert.match(s.node('#nav').innerHTML,/对话式 Agent/);
  assert.match(s.node('#nav').innerHTML,/蓝图版工作流/);
  assert.doesNotMatch(s.node('#nav').innerHTML,/工作看台|提案示例|蓝图库示例/);
});
test("Agent and blueprint navigation emits route once without bounce, retaining shared host",()=>{
  const s=shell();const host=s.node('#product-module');
  s.click('blueprint');assert.equal(s.location.hash,'#blueprint');assert.equal(s.messages.at(-1).detail,'blueprint');
  s.click('agent');assert.equal(s.location.hash,'#agent');assert.equal(s.messages.at(-1).detail,'chat');
  assert.equal(s.node('#product-module'),host);
  s.click('overview');assert.equal(host.hidden,true);
  s.click('agent');assert.equal(host.hidden,false);
});
test("module navigation and browser history keep original product routes",()=>{
  const s=shell();s.events['product:route']({detail:'blueprint'});assert.equal(s.location.hash,'#blueprint');
  s.click('overview');s.events['product:route']({detail:'chat'});assert.equal(s.location.hash,'#overview');
  s.location.hash='#agent';s.events.popstate();assert.equal(s.messages.at(-1).detail,'chat');
});
test("product entry mounts native server-simulator components without authentication side effects",()=>{
  const source=readFileSync(new URL('../src/product.tsx',import.meta.url),'utf8');
  assert.match(source,/createServerDemoGateway\(/);assert.match(source,/<WorkspaceApp productMode/);
  assert.doesNotMatch(source,/LoginScreen|persistSession|createRemoteGateway|iframe/);
});
