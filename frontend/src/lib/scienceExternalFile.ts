export async function readExternalScienceJSON(file:File,maxBytes:number):Promise<{fileName:string;documentBase64:string}> {
 if(!/\.json$/i.test(file.name))throw new Error('请选择 JSON 文件。');
 if(!file.size||file.size>maxBytes)throw new Error(`JSON 文件不能为空，且不得超过 ${Math.floor(maxBytes/1024)} KiB。`);
 const bytes=new Uint8Array(await file.arrayBuffer());
 if(!bytes.length||bytes.length>maxBytes)throw new Error(`JSON 文件不能为空，且不得超过 ${Math.floor(maxBytes/1024)} KiB。`);
 let parsed:unknown;
 try {
  // Keep BOM in the original bytes used for backend hashing; only the parser
  // skips one leading BOM. Invalid UTF-8 is never silently replaced.
  const text=new TextDecoder('utf-8',{fatal:true,ignoreBOM:true}).decode(bytes);
  parsed=JSON.parse(text.replace(/^\uFEFF/,''));
 }catch{throw new Error('文件须为有效的 UTF-8 JSON。');}
 if(!parsed||Array.isArray(parsed)||typeof parsed!=='object')throw new Error('JSON 须包含完整的数据对象。');
 const parts:string[]=[];
 for(let offset=0;offset<bytes.length;offset+=8192)parts.push(Array.from(bytes.subarray(offset,offset+8192),value=>String.fromCharCode(value)).join(''));
 return {fileName:file.name,documentBase64:btoa(parts.join(''))};
}
