import type {ScienceResult} from '../lib/scienceGateway';

const finite=(v:unknown):v is number=>typeof v==='number'&&Number.isFinite(v);
const percent=(v:number)=>`${(v*100).toFixed(2)}%`;
const points=(v:number)=>`${v.toFixed(2)} 个百分点`;
type Condition={counts:number[][];n0:number;n1:number;e0:number;e1:number;fidelity:number;perCapture:Record<string,number>;standardError?:number};
function condition(value:any):Condition|undefined{
 const rows=value?.counts;
 if(!Array.isArray(rows)||rows.length!==2||rows.some(row=>!Array.isArray(row)||row.length!==2||row.some(v=>!Number.isSafeInteger(v)||v<0||v>1e9)))return;
 const n0=rows[0][0]+rows[0][1],n1=rows[1][0]+rows[1][1];if(!n0||!n1||value.n0!==n0||value.n1!==n1)return;
 const e0=rows[0][1]/n0,e1=rows[1][0]/n1;
 const original=value.perCaptureFidelity;
 const perCapture:Record<string,number>={};
 if(original&&typeof original==='object'&&Object.keys(original).length<=12)for(const [key,v] of Object.entries(original))if(/^(?:[0-9]|1[01])$/.test(key)&&finite(v)&&v>=0&&v<=1)perCapture[key]=v;
 return {counts:rows,n0,n1,e0,e1,fidelity:1-(e0+e1)/2,perCapture,standardError:finite(value.standardErrorBinomial)&&value.standardErrorBinomial>=0?value.standardErrorBinomial:undefined};
}
function Counts({value,label}:{value:Condition;label:string}){
 return <section><h5>{label}</h5><table aria-label={`${label} 验证样本计数`}><thead><tr><th>搁置前逻辑态</th><th>判为 0</th><th>判为 1</th><th>样本数</th></tr></thead><tbody>{value.counts.map((row,i)=><tr key={i}><th>制备 |{i}⟩</th><td>{row[0]}</td><td>{row[1]}</td><td>{i===0?value.n0:value.n1}</td></tr>)}</tbody></table><p>e₀ = {percent(value.e0)} · e₁ = {percent(value.e1)} · 等权 F = {percent(value.fidelity)}</p>{value.standardError!==undefined&&<small>计数二项标准误：{points(100*value.standardError)}</small>}</section>;
}
function captures(value:unknown):number[]|undefined{
 if(!Array.isArray(value)||!value.length||value.length>12||value.some(n=>!Number.isInteger(n)||n<0||n>11)||new Set(value).size!==value.length)return;
 return value;
}
function Acquisition({result,title}:{result:ScienceResult;title:string}){
 const off=condition(result.comparison?.off),on=condition(result.comparison?.on);
 if(!off||!on)return <section><h4>{title}</h4><p className="science-note">这条记录缺少完整分类计数，详见原始回执。</p></section>;
 const protocol=result.classifierProtocol;
 const training=captures(protocol?.trainingCaptures),validation=captures(protocol?.validationCaptures);
 const splitValid=training&&validation&&!training.some(n=>validation.includes(n));
 const paired=(splitValid?validation.map(String):[]).filter(k=>finite(off.perCapture[k])&&finite(on.perCapture[k]));
 const stderr=result.metrics?.paired_capture_gain_stderr_pp;
 const identity=result.pulseIdentities?.probe12?.identity;
 return <section className="science-card"><h4>{title}</h4><div className="science-two-columns"><Counts value={off} label="关闭 SHELVE"/><Counts value={on} label="启用 SHELVE"/></div>
 <p><strong>启用 − 关闭：{points(100*(on.fidelity-off.fidelity))}</strong>。负值表示本次判别保真度下降。</p>
 <p>e₀ = n₀₁/N₀，e₁ = n₁₀/N₁，F = 1 − (e₀+e₁)/2；两类初态等权。</p>
 {splitValid?<><p>训练采集：{training.map(n=>n+1).join('、')}；验证采集：{validation.map(n=>n+1).join('、')}（编号从 1 起）。</p>{protocol.type==='separate condition LDA; equal priors; fixed training/validation capture split'&&<p>关闭／启用条件分别训练分类器，并使用留出采集验证。</p>}{protocol.logicalLabelsRetained===true&&<p>逻辑标签：搁置前的制备态。</p>}</>:<p className="science-note">分类器训练／验证分组未完整记录。</p>}
 {paired.length>0?<><table aria-label={`${title} 配对采集波动`}><thead><tr><th>验证采集</th><th>关闭 F</th><th>启用 F</th><th>启用 − 关闭</th></tr></thead><tbody>{paired.map(k=><tr key={k}><th>第 {Number(k)+1} 次</th><td>{percent(off.perCapture[k])}</td><td>{percent(on.perCapture[k])}</td><td>{points(100*(on.perCapture[k]-off.perCapture[k]))}</td></tr>)}</tbody></table>{finite(stderr)&&stderr>=0&&<p>同轮配对采集改善的标准误：{points(stderr)}（不是首测与独立复测的差值）。</p>}</>:<p>逐次验证采集保真度未记录，重复采集波动不可恢复。</p>}
 {identity&&<p>本轮 π₁₂ 探测脉冲：{finite(identity.frequencyHz)?`${(identity.frequencyHz/1e9).toFixed(6)} GHz`:'频率未记录'}；幅度 {finite(identity.amplitude)?`${identity.amplitude} a.u.`:'未记录'}；时长 {finite(identity.durationS)?`${(identity.durationS*1e9).toFixed(3)} ns`:'未记录'}。完整脉冲身份见参数与来源详情。</p>}
 </section>;
}

export default function ScienceShelveResult({result}:{result?:ScienceResult}){
 if(result?.procedureId!=='sq_shelve_readout_compare'||!['acquisition','real','measured','historical'].includes(result.source??result.dataKind??''))return null;
 const status=result.partial?'未完成':({passed:'通过（不等同人工接受）',failed:'未通过',unverified:'待完成独立验收'}[result.quality?.status as string]??'未记录');
 return <section aria-label="真实 SHELVE 留出读出结果"><h3>真实 SHELVE 留出读出对比</h3><p>保存时的控制器质量：{status}。当前来源资格以记录校验状态为准。</p>
 {result.shelveSelection&&<Acquisition result={result.shelveSelection} title="首测"/>}
 <Acquisition result={result} title={result.shelveStage==='verification'?'独立复测':result.shelveStage==='selection'?'首测':'采集结果（轮次未记录）'}/>
 <p className="science-note">这是逻辑态判别和条件 ADC 响应对比，不是 |2⟩ 布居、转移保真度或泄漏测量。手工输入计数另列为计算来源。</p></section>;
}
