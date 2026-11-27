import {useEffect,useState} from 'react';
import type {ComponentType} from 'react';
import type {ScienceRecord} from '../lib/scienceGateway';
import type {WorkspaceGateway} from '../lib/workspaceGateway';
import type {PlotSeries} from '../lib/scienceModels';

type Row = {status:string;reason?:string;recordId:string;operationId:string;rows:number;tones:number;
 row:number;tone:number;shots:number;shotIndices:number[];i:number[];q:number[];artifactId:string;sha256:string};

export function AttemptAdcEvidence({record,gateway,Plot}:{record:ScienceRecord;gateway:WorkspaceGateway;
 Plot:ComponentType<{series:PlotSeries[];label:string}>}){
 const [row,setRow]=useState(0),[tone,setTone]=useState(0);
 const [rowText,setRowText]=useState('1'),[toneText,setToneText]=useState('1');
 const [data,setData]=useState<Row>(),[error,setError]=useState(''),[generation,reload]=useState(0);
 const bound=record.readOnlyAttempt;
 useEffect(()=>{
  if(!bound?.adcAvailable)return;
  let current=true;setData(undefined);setError('');
  const query=new URLSearchParams({workflowId:bound.workflowId,nodeId:bound.nodeId,attemptId:bound.attemptId,row:String(row),tone:String(tone)});
  gateway.get<Row>('/science/attempt-signal-adc?'+query).then(result=>{
   if(!current)return;
   if(result.recordId!==record.id||result.operationId!==bound.operationId||
      (result.status==='available'&&(result.row!==row||result.tone!==tone)))throw new Error('所选归档与 ADC 回执不一致');
   setData(result);
  }).catch(e=>{if(current)setError(e instanceof Error?e.message:String(e));});
  return ()=>{current=false;};
 },[record.id,bound,gateway,row,tone,generation]);
 return <section aria-label="本轮原始 ADC"><h4>单发读出 I、Q</h4>
 {!bound?.adcAvailable?<p>本轮主结果没有登记可逐行查看的原始 ADC 汇总文件。</p>:
 error?<p role="alert">{error} <button onClick={()=>reload(n=>n+1)}>重试读取 ADC</button></p>:
 !data?<p>正在读取本轮原始 ADC。</p>:data.status!=='available'?<p>{data.reason}</p>:<>
 <form className="science-form" onSubmit={e=>{e.preventDefault();const r=Number(rowText),t=Number(toneText);
  if(Number.isInteger(r)&&r>=1&&r<=data.rows&&Number.isInteger(t)&&t>=1&&t<=data.tones){setRow(r-1);setTone(t-1);}}}>
 <label className="science-field"><span>采集行（1–{data.rows}）</span><input required type="number" min={1} max={data.rows}
  value={rowText} onChange={e=>setRowText(e.currentTarget.value)}/></label>
 {data.tones>1&&<label className="science-field"><span>解调频点（1–{data.tones}）</span><input required type="number" min={1} max={data.tones}
  value={toneText} onChange={e=>setToneText(e.currentTarget.value)}/></label>}
 <button type="submit">查看所选行</button></form>
 <Plot label={'第 '+(row+1)+' 行的单发读出 I、Q'} series={(['i','q'] as const).map(key=>({name:key.toUpperCase(),x:data.shotIndices.map(i=>i+1),y:data[key],xUnit:'单发序号',yUnit:'ADC 读出值',source:'acquisition'}))}/>
 <p className="science-note">每个点对应一发的解调读出值。此图的横轴是单发序号，原始 ADC 时域波形未随这个数组保存。按归档行顺序查看，每行 {data.shots} 发，图中显示 {data.shotIndices.length} 发。</p>
 <details className="science-details"><summary>原始文件来源</summary><p>{data.artifactId}</p><p>SHA-256 {data.sha256}</p></details>
 </>}</section>;
}
