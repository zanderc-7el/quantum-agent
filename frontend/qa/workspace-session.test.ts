import test from "node:test";
import assert from "node:assert/strict";
import { hasWorkspaceSession } from "../src/lib/workspaceSession.ts";
import { createRoleGateway } from "../src/lib/roleGateway.ts";
import { createMockGateway } from "../src/lib/gateway.ts";
test("only explicit mock product mode allows session-free demo actions", () => {
  assert.equal(hasWorkspaceSession(true,"mock",undefined,"leader"),true);
  for(const product of [false,true]) for(const mode of [undefined,"remote"] as const)
    assert.equal(hasWorkspaceSession(product,mode,undefined,"leader"),false);
  assert.equal(hasWorkspaceSession(false,"mock",undefined,"leader"),false);
  assert.equal(hasWorkspaceSession(false,"remote","leader","leader"),true);
});
test("session-free product creates and starts mock workflow, while standalone refuses", async () => {
 const user={id:"leader",username:"leader",displayName:"演示",role:"leader" as const};
 const mock=createMockGateway({save:()=>{}});
 const gateway=createRoleGateway(user,mock,undefined,()=>hasWorkspaceSession(true,mock.mode,undefined,user.id));
 const w=await gateway.create({name:"演示创建回归",machine:"M7",experiment:"single",qubits:1,target:"Q12",type:"adaptive",policy:"threshold",scenario:"normal"});
 assert.equal(w.name,"演示创建回归");
 assert.equal((await gateway.act(w.id,"start")).status,"running");
 const standalone=createRoleGateway(user,mock,undefined,()=>hasWorkspaceSession(false,mock.mode,undefined,user.id));
 await assert.rejects(standalone.act(w.id,"pause"),/登录已过期/);
});
