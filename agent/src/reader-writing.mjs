import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {record,validateInput} from './validation.mjs';

export const WRITING_POLICY_VERSION='defensive-language-audit/report-reader-v1';
let policy;
export function readerWritingPolicy(){
  if(policy)return policy;
  const projectRoot=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
  const root=path.join(projectRoot,'knowledge/report-writing/defensive-language-audit');
  const skill=fs.readFileSync(path.join(root,'SKILL.md'),'utf8');
  const prompt=fs.readFileSync(path.join(root,'references/prompt-template.md'),'utf8');
  policy={version:WRITING_POLICY_VERSION,skillSha256:createHash('sha256').update(skill).digest('hex'),
    guidance:`写作规范：${WRITING_POLICY_VERSION}。正文面向正在判断实验进展的人，先说结论，再说决定下一步所需的证据。使用中文指标名、单位和简洁段落。不要把内部JSON、字段清单、UUID或审查过程当作回答正文。必要的未通过、历史范围和不确定性放在对应结论旁边。不要反复解释系统规则。\n用户已授权整理文字与排版；没有授权改变事实、因果、验收状态、责任或证据强弱。不能声称人工已接受自动审查。\n附用户指定的规范原文与审查格式：\n${skill}\n${prompt}`};
  return policy;
}

export const readerAuditDefinition={name:'audit_reader_text',inputSchema:{type:'object',required:['readerDecision','claims','sentences','retainedLimitations','unresolvedQuestions'],properties:{
  readerDecision:{type:'string'},claims:{type:'array',items:{type:'object'}},
  sentences:{type:'array',items:{type:'object'}},retainedLimitations:{type:'array',items:{type:'string'}},
  unresolvedQuestions:{type:'array',items:{type:'string'}},
},additionalProperties:false}};

export const readerAuditInstructions=`当前实验的回答采用两步写作。Pass A先调用audit_reader_text，Pass B只输出工具返回的approvedText作为final.content。工具参数：readerDecision；claims=[{claim,evidenceIds,scope,limitation}]，evidenceIds引用上下文已有ID或snapshot；sentences=[{text,function,consumer,decision,ifRemoved,action,revision}]；retainedLimitations；unresolvedQuestions。function可为claim/evidence/reasoning/limitation/signposting/self_reference/reader_engagement/apology/generic_disclaimer/repeated_meta。action为KEEP/DROP/CALIBRATE/RELOCATE。涉及事实、推理、范围或必要限制的句子只允许KEEP或原文RELOCATE；只有不承担事实作用的套话可删减。KEEP/RELOCATE的revision必须等于text，DROP的revision为空。sentences顺序就是最终输出顺序，保留换行。正文通常一至三段，不需要专门列出所有约束。工具只审查文字动作，不证明测量事实，也不代表人工审定。`;

const functions=new Set(['claim','evidence','reasoning','limitation','signposting','self_reference','reader_engagement','apology','generic_disclaimer','repeated_meta']);
const material=new Set(['claim','evidence','reasoning','limitation']);
const text=(v,max=6000)=>typeof v==='string'&&v.trim().length>0&&v.length<=max;
function contextIds(context){
  const ids=new Set(['snapshot']);
  const visit=value=>{if(Array.isArray(value))value.forEach(visit);else if(record(value)){
    for(const key of ['id','artifactId','attemptId','evidenceId','nodeId','workflowId','operationId'])if(typeof value[key]==='string')ids.add(value[key]);
    Object.values(value).forEach(visit);
  }};visit(context);return ids;
}
export function auditReaderText(args,context){
  validateInput(args);
  if(Object.keys(args).some(key=>!Object.hasOwn(readerAuditDefinition.inputSchema.properties,key)))throw new Error('unknown_writing_audit_field');
  if(!text(args.readerDecision,2000)||!Array.isArray(args.claims)||args.claims.length>30||!Array.isArray(args.sentences)||!args.sentences.length||args.sentences.length>40)throw new Error('invalid_writing_audit');
  for(const key of ['retainedLimitations','unresolvedQuestions'])if(!Array.isArray(args[key])||args[key].length>30||args[key].some(v=>!text(v)))throw new Error('invalid_writing_audit');
  const ids=contextIds(context);
  for(const claim of args.claims){
    if(!record(claim)||Object.keys(claim).some(k=>!['claim','evidenceIds','scope','limitation'].includes(k))||!text(claim.claim)||!text(claim.scope)||typeof claim.limitation!=='string'||claim.limitation.length>6000||!Array.isArray(claim.evidenceIds)||claim.evidenceIds.length>30||claim.evidenceIds.some(id=>!ids.has(id)))throw new Error('invalid_writing_claim');
    if(!claim.evidenceIds.length&&!args.unresolvedQuestions.length)throw new Error('writing_claim_requires_evidence');
  }
  for(const sentence of args.sentences){
    if(!record(sentence)||Object.keys(sentence).some(k=>!['text','function','consumer','decision','ifRemoved','action','revision'].includes(k))||!text(sentence.text)||!functions.has(sentence.function)||!text(sentence.consumer,2000)||!text(sentence.decision,2000)||!text(sentence.ifRemoved,2000)||!['KEEP','DROP','CALIBRATE','RELOCATE'].includes(sentence.action)||typeof sentence.revision!=='string'||sentence.revision.length>6000)throw new Error('invalid_writing_sentence');
    if(material.has(sentence.function)&&!['KEEP','RELOCATE'].includes(sentence.action))throw new Error('material_edit_requires_author');
    if(['KEEP','RELOCATE'].includes(sentence.action)&&sentence.revision!==sentence.text)throw new Error('unapproved_claim_rewrite');
    if(sentence.action==='DROP'&&sentence.revision!=='')throw new Error('drop_revision_must_be_empty');
    // Numeric evidence is never silently removed by classifying it as generic prose.
    if(['DROP','CALIBRATE'].includes(sentence.action)&&/\d/.test(sentence.text))throw new Error('numeric_edit_requires_author');
    if(sentence.action==='CALIBRATE'&&!sentence.revision.trim())throw new Error('empty_calibration');
  }
  const approvedText=args.sentences.filter(s=>s.action!=='DROP').map(s=>s.revision).join('\n\n');
  if(!approvedText.trim()||approvedText.length>8000)throw new Error('invalid_approved_text');
  const info=readerWritingPolicy();
  return {ok:true,policyVersion:info.version,skillSha256:info.skillSha256,approvedText,
    humanAccepted:false,factsIndependentlyVerified:false,
    note:'仅批准已授权的文字整理；事实与验收状态仍以控制器证据为准，未代表人工接受。'};
}
