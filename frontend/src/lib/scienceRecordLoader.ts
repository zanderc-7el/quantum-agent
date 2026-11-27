import type {ScienceRecord} from './scienceGateway.ts';

/** One detail request per selection/refresh generation, including failed loads. */
export function createScienceRecordLoader(fetchRecord:(id:string)=>Promise<ScienceRecord>,publish:(record:ScienceRecord)=>void,onError:(error:unknown)=>void){
 let generation=0;const requested=new Set<string>();
 return {
  reset(){generation++;requested.clear();},
  forget(id:string){requested.delete(id);},
  load(id:string){
   if(!id||requested.has(id))return;
   requested.add(id);const current=generation;
   void Promise.resolve().then(()=>fetchRecord(id)).then(record=>{
    if(current!==generation)return;
    if(!record||record.id!==id||record.summary===true)throw new Error('记录详情身份不一致，请刷新来源记录');
    publish(record);
   }).catch(error=>{if(current===generation)onError(error);});
  },
 };
}
