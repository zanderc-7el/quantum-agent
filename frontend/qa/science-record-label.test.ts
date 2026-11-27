import test from 'node:test';
import assert from 'node:assert/strict';
import {scienceRecordLabel,scienceRecordTimes} from '../src/lib/scienceRecordLabel.ts';

const base:any={id:'record',kind:'measurement',label:'采集记录',scope:{resourceId:'Q11'},input:{},createdAt:'2026-11-26T03:18:47+08:00'};
test('actual method wins over the shared Rabi blueprint node and generic record title',()=>{
 assert.equal(scienceRecordLabel({...base,result:{procedureId:'sq_rabi_time',nodeLabel:'幅度 Rabi'}}),'Q11 · 时间 Rabi');
 assert.equal(scienceRecordLabel({...base,provenance:{procedureId:'sq_rabi_amplitude'}}),'Q11 · 幅度 Rabi');
});
test('missing metadata falls back to explicit human names and then kind without exposing IDs',()=>{
 assert.equal(scienceRecordLabel({...base,result:{procedureId:'future_method',methodLabel:'新测量方法'}}),'Q11 · 新测量方法');
 assert.equal(scienceRecordLabel({...base,result:{nodeLabel:'工作点复核'}}),'Q11 · 工作点复核');
 assert.equal(scienceRecordLabel({...base,label:'独立频率复测'}),'Q11 · 独立频率复测');
 assert.equal(scienceRecordLabel({...base,scope:{},result:{procedureId:'internal_unknown_code'}}),'采集记录');
 assert.equal(scienceRecordLabel({...base,kind:'simulation',label:'同点模拟',result:{experimentBinding:{procedureId:'sq_rabi_time'}}}),'Q11 · 时间 Rabi · 模型预测');
});
test('acquired time and archive time retain their distinct meanings without inventing a missing acquisition date',()=>{
 const dates=scienceRecordTimes({...base,result:{acquiredAt:'2026-11-18T12:26:27+08:00'}});
 assert.match(dates,/原始采集时间.*11\/18/);assert.match(dates,/归档于.*11\/26/);
 assert.match(scienceRecordTimes(base),/^归档于/);assert.doesNotMatch(scienceRecordTimes(base),/原始采集时间/);
 assert.equal(scienceRecordTimes({...base,createdAt:undefined}),'归档于 时间未记录');
 assert.match(scienceRecordTimes({...base,kind:'simulation',result:{acquiredAt:'2026-11-18T12:26:27+08:00'}}),/^保存于/);
});
