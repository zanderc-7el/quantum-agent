import {loadConfig,ROOT} from './config.mjs';
import {Store} from './store.mjs';
import {Brain} from './brain.mjs';
import fs from 'node:fs';import path from 'node:path';
const config=loadConfig();const store=new Store(config.dbPath);const brain=new Brain(config,store);
const result=await brain.run('decide',{runId:'connection-probe',nodeId:'s02',revision:0,mode:'replay',goal:'连接验证：观察尚无可信比特响应，选择wait；不得称为实测或执行仪器。',observations:[{id:'probe-evidence',quality:'unverified',note:'synthetic connection probe, not experimental evidence'}],allowedActions:['wait'],parameterBounds:{},rules:[],history:[]});
const report={checkedAt:new Date().toISOString(),health:brain.health(),result};fs.mkdirSync(path.join(ROOT,'runtime'),{recursive:true});fs.writeFileSync(path.join(ROOT,'runtime','connection-probe.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));store.close();if(result.status!=='ok')process.exitCode=1;
