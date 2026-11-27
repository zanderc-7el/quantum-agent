// Bounded, lossless pagination for the evidence reader. No new scientific claims.
export function chunks(items, size) {
  if (!Number.isInteger(size) || size < 1) throw Error('Invalid page size');
  return Array.from({length:Math.ceil(items.length/size)}, (_, i) => items.slice(i*size, (i+1)*size));
}
export function presentationSummary(node) {
  const value=node.presentationSummary??node.summary??'';
  return value.length<=130?value:`${node.label}：${node.status}。完整诊断和候选归属见后续说明页。`;
}
export function diagnosticPages(nodes) {
  return nodes.flatMap(node=>{
    if (!node.summary || node.summary===presentationSummary(node)) return [];
    return chunks(Array.from(node.summary), 430).map((part,index)=>({node,index,text:part.join('')}));
  });
}
export function acquisitionPages(model) { return chunks(model.acquisitionHistory??[],4); }
