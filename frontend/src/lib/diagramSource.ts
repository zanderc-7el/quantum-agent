// Match only the complete provenance string emitted by document_diagrams.py.
// A partial match must never rewrite a manually entered source.
const importedDocxSource = /^([^/\\:\u0000\r\n]+\.docx)；(图\s*[0-9一二三四五六七八九十百]+(?:[.．\-－][0-9]+)*\s*[:：、.．]?\s*芯片(?:设计)?版图)；word\/document\.xml body\[[1-9][0-9]*\]；word\/media\/[^/\\:?#%；\r\n]+\.(?:png|jpe?g)；导入记录 import-[a-f0-9]{32}；文档 SHA-256 [a-f0-9]{64}；图片 SHA-256 [a-f0-9]{64}；设计原图，物理身份及耦合关系未确认$/i;

export function importedDiagramSourceLabel(source?: string): string | undefined {
 const match=source?.match(importedDocxSource);
 return match && match[0] === source ? `${match[1]} · ${match[2]}` : undefined;
}

export function diagramSourceLabel(source?: string): string | undefined {
 return importedDiagramSourceLabel(source) ?? source;
}
