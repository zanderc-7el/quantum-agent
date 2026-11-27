import test from 'node:test';
import assert from 'node:assert/strict';
import {diagramSourceLabel,importedDiagramSourceLabel} from '../src/lib/diagramSource.ts';
import {generatedDiagramSource,readableDiagramSource} from './fixtures/generated-diagram-source.ts';

test('only a complete backend DOCX provenance contract gets a readable label',()=>{
 assert.equal(importedDiagramSourceLabel(generatedDiagramSource),readableDiagramSource);
 assert.equal(diagramSourceLabel(generatedDiagramSource),readableDiagramSource);
 for(const source of [undefined,'','原图来源：图1',
  generatedDiagramSource.replace('import-0123456789abcdef0123456789abcdef','manual-123'),
  generatedDiagramSource.replace('a'.repeat(64),'a'.repeat(63)),
  generatedDiagramSource.replace('word/media/image2.png','https://example.test/image2.png'),
  generatedDiagramSource.replace('图1 芯片设计版图','图1 接线图'),
  generatedDiagramSource.replace('；设计原图，物理身份及耦合关系未确认',''),
  generatedDiagramSource+'；人工补充：实物有差异',generatedDiagramSource+'\n']){
  assert.equal(importedDiagramSourceLabel(source),undefined);
  assert.equal(diagramSourceLabel(source),source,'manual or differently structured evidence must remain verbatim');
 }
});
