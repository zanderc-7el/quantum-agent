// Mirrors backend/document_diagrams.py; import ID and digests are test fixtures.
export const documentName = 'TGQ-CD1QV2.03-SATA-P260000-01-33-47#.docx';
export const diagramCaption = '图1 芯片设计版图';
export const generatedDiagramSource = `${documentName}；${diagramCaption}；word/document.xml body[73]；word/media/image2.png；导入记录 import-0123456789abcdef0123456789abcdef；文档 SHA-256 ${'a'.repeat(64)}；图片 SHA-256 ${'b'.repeat(64)}；设计原图，物理身份及耦合关系未确认`;
export const readableDiagramSource = `${documentName} · ${diagramCaption}`;
