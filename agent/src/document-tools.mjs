// Stored document records only. No filesystem paths, URL fetching, or mutation.
const pick=(value,keys)=>Object.fromEntries(keys.filter(k=>value?.[k]!==undefined).map(k=>[k,value[k]]));
const validId=value=>typeof value==='string'&&/^[A-Za-z0-9_.:-]{1,160}$/.test(value)&&!value.includes('..');

export function validateDocumentArguments(args,reading=false){
 const allowed=reading?['documentId','query','offset','limit']:['query','offset','limit'];
 if(!args||Object.keys(args).some(k=>!allowed.includes(k))||(reading&&!validId(args.documentId))
  ||(args.query!==undefined&&(typeof args.query!=='string'||!args.query.trim()||args.query.length>200))
  ||(args.offset!==undefined&&(!Number.isSafeInteger(args.offset)||args.offset<0))
  ||(args.limit!==undefined&&(!Number.isSafeInteger(args.limit)||args.limit<1||args.limit>20)))throw new Error('runtime_tool_arguments_invalid');
}

export function documentMetadata(document){
 return {...pick(document,['id','revision','filename','status','sha256','createdAt','updatedAt']),
  name:document.filename??document.name??document.draft?.name??document.id,
  blockCount:document.blocks?.length??0};
}

function page(total,matching,offset,returned){
 const nextOffset=offset+returned<matching?offset+returned:null;
 return {total,matching,offset,returned,nextOffset,complete:nextOffset===null};
}

export function documentDirectory(documents,args={}){
 validateDocumentArguments(args);
 const query=args.query?.trim().toLocaleLowerCase(),offset=args.offset??0,limit=args.limit??12;
 const matches=documents.filter(d=>!query||[d.id,d.filename,d.name,d.draft?.name].some(v=>String(v??'').toLocaleLowerCase().includes(query)));
 const items=matches.slice(offset,offset+limit).map(documentMetadata);
 return {page:page(documents.length,matches.length,offset,items.length),items};
}

export function documentExcerpt(document,args={}){
 validateDocumentArguments({...args,documentId:document.id},true);
 const query=args.query?.trim().toLocaleLowerCase(),offset=args.offset??0,limit=args.limit??8;
 const all=document.blocks??[],matches=all.filter(b=>!query||String(b.text??'').toLocaleLowerCase().includes(query));
 const blocks=matches.slice(offset,offset+limit).map(block=>{
  const text=String(block.text??''),match=query?text.toLocaleLowerCase().indexOf(query):0;
  const start=Math.max(0,match-150),end=Math.min(text.length,start+600);
  return {...pick(block,['id','location','kind']),text:text.slice(start,end),
   textRange:{start,end,total:text.length},textTruncated:start>0||end<text.length};
 });
 const draft=document.draft??{},resources=draft.qubits??[],design=draft.designResources??[];
 const relevant=resource=>query&&(String(resource.id??'').toLocaleLowerCase()===query||String(resource.label??'').toLocaleLowerCase().includes(query));
 return {...documentMetadata(document),page:page(all.length,matches.length,offset,blocks.length),blocks,
  draft:{name:draft.name,resourceCount:resources.length,resourceIds:resources.map(r=>r.id),
   qubits:resources.filter(relevant),designResources:design.filter(relevant)},
  contentKind:'stored_document_extraction',calibrationEvidence:false};
}

// The final observation budget may shorten a page. Do not claim omitted or
// partially serialized rows were returned, or advance a cursor past them.
export function finishDocumentPage(original,output){
 const key=Array.isArray(original.items)?'items':'blocks';
 const expected=new Map(original[key].map(row=>[row.id,row]));
 output[key]=(output[key]??[]).filter(row=>{
  const full=expected.get(row?.id);
  return full&&(key==='items'?row.filename===full.filename&&row.revision===full.revision&&row.sha256===full.sha256
   :row.text===full.text&&row.location===full.location&&JSON.stringify(row.textRange)===JSON.stringify(full.textRange));
 });
 output.page=page(original.page.total,original.page.matching,original.page.offset,output[key].length);
}
