import base64,io,json,tempfile,unittest,zipfile
from pathlib import Path
from backend.store import Store
from backend.chip_catalog import ChipCatalog
from backend.common import APIError
try: from backend.document_imports import DocumentImports
except ImportError: DocumentImports=None

def _local_fixture(test, name):
 path=Path(__file__).parent/'fixtures'/name
 if not path.is_file():test.skipTest('Private local fixture not included in the source export')
 return path

def docx_bytes():
 b=io.BytesIO()
 with zipfile.ZipFile(b,'w') as z:
  # Replays must resend identical request bytes even across ZIP's 2-second clock tick.
  z.writestr(zipfile.ZipInfo('[Content_Types].xml'),'<Types/>')
  z.writestr(zipfile.ZipInfo('word/document.xml'),'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Q3 readout 7.1 GHz drive 4.8 GHz</w:t></w:r></w:p></w:body></w:document>')
 return b.getvalue()
class DocumentImportTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.store=Store(self.root/'data/test.db',root=self.root);self.cat=ChipCatalog(self.store,self.root,{})
 def tearDown(self):self.tmp.cleanup()
 def service(self,brain=None):
  self.assertIsNotNone(DocumentImports,'document parser is missing');return DocumentImports(self.store,self.root,self.cat,brain)
 def test_actual_docx_parse_persist_and_edit(self):
  s=self.service();body={'operationId':'up','filename':'chip.docx','contentBase64':base64.b64encode(docx_bytes()).decode()};d=s.create(body)
  self.assertIn('Q3 readout 7.1 GHz',d['blocks'][0]['text']);self.assertEqual(s.create(body),d)
  d=s.update(d['id'],{'operationId':'edit','expectedRevision':1,'draft':{'name':'edited','qubits':[]}})
  self.assertEqual(self.service().get(d['id'])['draft']['name'],'edited')
  with self.assertRaises(APIError):s.update(d['id'],{'operationId':'stale','expectedRevision':1,'draft':{}})
 def test_model_cannot_confirm_identity_or_add_device_bounds(self):
  def brain(path,payload):
   return {'message':json.dumps({'draft':{'name':'from model','parameterBounds':{'drive_hz':[0,999e9]},'qubits':[{'id':'Q3','label':'alias','physicalId':'guessed','identityStatus':'confirmed','parameters':{'drive_hz':4.8e9}}]}})}
  s=self.service(brain);d=s.create({'operationId':'up','filename':'x.docx','contentBase64':base64.b64encode(docx_bytes()).decode()})
  a=s.analyze(d['id'],{'operationId':'model','expectedRevision':1})
  self.assertEqual(a['draft']['qubits'][0]['identityStatus'],'unresolved');self.assertNotIn('parameterBounds',a['draft'])
  self.assertEqual(a['draft']['qubits'][0]['provenance']['drive_hz']['sourceId'],d['id'])
 def test_spoofed_magic_and_traversal_and_zip_bomb_rejected(self):
  s=self.service()
  for name,raw in [('../x.docx',docx_bytes()),('x.pdf',docx_bytes()),('x.exe',b'MZ')]:
   with self.assertRaises(APIError):s.create({'operationId':name,'filename':name,'contentBase64':base64.b64encode(raw).decode()})
  b=io.BytesIO()
  with zipfile.ZipFile(b,'w',zipfile.ZIP_DEFLATED) as z:z.writestr('word/document.xml','a'*1000000)
  with self.assertRaises(APIError):s.create({'operationId':'bomb','filename':'x.docx','contentBase64':base64.b64encode(b.getvalue()).decode()})
if __name__=='__main__':unittest.main()

class ActualFormatTests(DocumentImportTests):
 def test_pptx_slide_locations(self):
  b=io.BytesIO()
  with zipfile.ZipFile(b,'w') as z:
   z.writestr('ppt/presentation.xml','<presentation/>')
   z.writestr('ppt/slides/slide2.xml','<a:root xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:p><a:r><a:t>Q5 bus B2</a:t></a:r></a:p></a:root>')
  d=self.service().create({'operationId':'ppt','filename':'chip.pptx','contentBase64':base64.b64encode(b.getvalue()).decode()})
  self.assertEqual(d['blocks'][0]['text'],'Q5 bus B2');self.assertIn('slide2',d['blocks'][0]['location'])
 def test_xlsx_cells_pdf_pages_and_actual_local_ocr(self):
  import backend.document_imports
  import openpyxl,fitz
  from PIL import Image,ImageDraw,ImageFont
  s=self.service();wb=openpyxl.Workbook();ws=wb.active;ws.title='chip';ws.append(['Q8','7.2 GHz']);buf=io.BytesIO();wb.save(buf)
  d=s.create({'operationId':'xlsx','filename':'chip.xlsx','contentBase64':base64.b64encode(buf.getvalue()).decode()})
  self.assertIn('A1=Q8',d['blocks'][0]['text']);self.assertIn('chip',d['blocks'][0]['location'])
  pdf=fitz.open();p=pdf.new_page();p.insert_text((72,72),'Q8 drive 4.7 GHz');raw=pdf.tobytes();pdf.close()
  d=s.create({'operationId':'pdf','filename':'chip.pdf','contentBase64':base64.b64encode(raw).decode()})
  self.assertTrue(any('Q8 drive' in b['text'] for b in d['blocks']))
  image=Image.new('RGB',(1000,160),'white');draw=ImageDraw.Draw(image);font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',48);draw.text((20,35),'Q8 DRIVE 4.7 GHz',font=font,fill='black');buf=io.BytesIO();image.save(buf,format='PNG')
  from unittest.mock import patch
  with patch('socket.socket.connect',side_effect=AssertionError('OCR must remain offline')):
   d=s.create({'operationId':'ocr','filename':'scan.png','contentBase64':base64.b64encode(buf.getvalue()).decode()})
  self.assertTrue(any('4.7' in b['text'] for b in d['blocks']),d['blocks'])

class ImportConcurrencyTests(DocumentImportTests):
 def upload(self,s,operation='upload'):
  return s.create({'operationId':operation,'filename':'chip.docx','contentBase64':base64.b64encode(docx_bytes()).decode()})
 def test_model_pending_does_not_block_writer_or_duplicate_and_cas_preserves_edit(self):
  import threading
  entered=threading.Event();release=threading.Event();written=threading.Event();errors=[];calls=[]
  def brain(path,payload):
   calls.append(payload);entered.set();release.wait(5)
   return {'draft':{'name':'model stale','qubits':[]}}
  s=self.service(brain);d=self.upload(s);body={'operationId':'model-op','expectedRevision':1}
  def analyze():
   try:s.analyze(d['id'],body)
   except Exception as e:errors.append(e)
  thread=threading.Thread(target=analyze);thread.start()
  def writer():
   with self.store.transaction() as rt:rt.db.execute("INSERT INTO platform_meta VALUES('unrelated','committed')")
   written.set()
  try:
   self.assertTrue(entered.wait(2));w=threading.Thread(target=writer);w.start()
   self.assertTrue(written.wait(1),'model request holds the SQLite write transaction')
   with self.assertRaises(APIError) as raised:self.service(brain).analyze(d['id'],body)
   self.assertEqual(raised.exception.code,'IMPORT_OPERATION_PENDING')
   edited=s.update(d['id'],{'operationId':'manual','expectedRevision':1,'draft':{'name':'manual saved','qubits':[]}})
  finally:release.set();thread.join(5);w.join(5)
  self.assertEqual(s.get(d['id'])['draft']['name'],'manual saved');self.assertEqual(errors[0].code,'REVISION_CONFLICT')
  with self.assertRaises(APIError) as raised:self.service(brain).analyze(d['id'],body)
  self.assertEqual(raised.exception.code,'REVISION_CONFLICT');self.assertEqual(len(calls),1)
 def test_local_parsing_does_not_hold_writer_and_replay_survives_restart(self):
  import threading
  s=self.service();entered=threading.Event();release=threading.Event();written=threading.Event();out=[];original=s._parse
  def parse(ext,raw):entered.set();release.wait(5);return original(ext,raw)
  s._parse=parse
  thread=threading.Thread(target=lambda:out.append(self.upload(s)));thread.start()
  def writer():
   with self.store.transaction() as rt:rt.db.execute("INSERT INTO platform_meta VALUES('parse-writer','committed')")
   written.set()
  try:
   self.assertTrue(entered.wait(2));w=threading.Thread(target=writer);w.start();self.assertTrue(written.wait(1),'local parser holds write transaction')
  finally:release.set();thread.join(5);w.join(5)
  self.assertEqual(self.upload(self.service()),out[0]);self.assertEqual(len(self.service().list()),1)
 def test_more_than_1024_blocks_are_all_sent_in_bounded_chunks(self):
  calls=[]
  def brain(path,payload):calls.append(payload);return {'draft':{'name':'all blocks','qubits':[]}}
  s=self.service(brain);d=self.upload(s)
  with self.store.transaction() as rt:
   d['blocks']=[{'id':'block-'+str(i),'location':'page '+str(i),'text':'原始证据'*30} for i in range(1025)]
   rt.db.execute('UPDATE document_imports SET payload=? WHERE id=?',(json.dumps(d),d['id']))
  result=s.analyze(d['id'],{'operationId':'oversize','expectedRevision':1})
  self.assertGreater(len(calls),1)
  self.assertEqual([b['id'] for call in calls for b in call['blocks']],[b['id'] for b in d['blocks']])
  self.assertEqual(len(result['blocks']),1025)

class ImportBudgetTests(DocumentImportTests):
 def test_long_block_splits_preserve_text_offsets_and_both_agent_budgets(self):
  from backend.common import dumps
  calls=[]
  def brain(*args):calls.append(args[1]);return {'draft':{'name':'done','qubits':[]}}
  s=self.service(brain);d=s.create({'operationId':'upload-budget','filename':'chip.docx','contentBase64':base64.b64encode(docx_bytes()).decode()})
  for index,text in enumerate(('中'*81000,'x'*191000)):
   with self.store.transaction() as rt:
    d['blocks']=[{'id':'b1','location':'page 1','text':text}];rt.db.execute('UPDATE document_imports SET payload=? WHERE id=?',(json.dumps(d),d['id']))
   previous=len(calls);body={'operationId':'budget-'+str(index),'expectedRevision':d['revision']}
   result=s.analyze(d['id'],body);self.assertEqual(s.analyze(d['id'],body),result)
   sent=calls[previous:];self.assertGreater(len(sent),1)
   self.assertEqual(''.join(b['text'] for call in sent for b in call['blocks']),text)
   for call in sent:
    serialized=dumps(call);self.assertLessEqual(len(serialized.encode('utf-8')),240000);self.assertLessEqual(len(serialized.encode('utf-16-le'))//2,190000);self.assertLessEqual(len(call['blocks']),1024)
   self.assertEqual(s.get(d['id'])['blocks'][0]['text'],text)
   d=result
 def test_completed_analysis_replays_without_repeating_model(self):
  calls=[]
  def brain(path,payload):calls.append(payload);return {'draft':{'name':'extracted','qubits':[]}}
  s=self.service(brain);d=s.create({'operationId':'upload-replay','filename':'chip.docx','contentBase64':base64.b64encode(docx_bytes()).decode()})
  body={'operationId':'extract-replay','expectedRevision':1};result=s.analyze(d['id'],body)
  self.assertEqual(self.service(brain).analyze(d['id'],body),result);self.assertEqual(len(calls),1)

class HandoverDeckTests(DocumentImportTests):
 def test_large_handover_deck_preserves_slide_relationship_provenance_for_126_images(self):
  from unittest.mock import patch
  from PIL import Image
  image=io.BytesIO();Image.new('RGB',(8,8),'white').save(image,format='PNG')
  raw=io.BytesIO()
  with zipfile.ZipFile(raw,'w',zipfile.ZIP_STORED) as z:
   z.writestr('ppt/presentation.xml','<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><p:sldIdLst><p:sldId id="9" r:id="first"/><p:sldId id="2" r:id="second"/></p:sldIdLst></p:presentation>')
   z.writestr('ppt/_rels/presentation.xml.rels','<Relationships><Relationship Id="first" Target="slides/slide9.xml"/><Relationship Id="second" Target="slides/slide2.xml"/></Relationships>')
   z.writestr('ppt/slides/slide9.xml','<slide/>');z.writestr('ppt/slides/slide2.xml','<slide/>')
   rels=''.join('<Relationship Id="img'+str(i)+'" Target="../media/image'+str(i)+'.png"/>' for i in range(126))
   z.writestr('ppt/slides/_rels/slide9.xml.rels','<Relationships>'+rels+'</Relationships>')
   for i in range(126):z.writestr('ppt/media/image'+str(i)+'.png',image.getvalue())
   z.writestr('ppt/test-padding.bin',b'x'*(21*1024*1024))
  s=self.service()
  with patch.object(s,'_ocr',return_value=['fixture extracted text']):
   result=s.create({'operationId':'large-handover','filename':'handover.pptx','contentBase64':base64.b64encode(raw.getvalue()).decode()})
  imageblocks=[b for b in result['blocks'] if b.get('mediaPath')]
  self.assertEqual(len(imageblocks),126)
  self.assertEqual(imageblocks[0]['sourceSlides'],[{'slideNumber':1,'slidePath':'ppt/slides/slide9.xml','relationshipId':'img0'}])
  self.assertIn('slide 1',imageblocks[0]['location'])
 def test_office_media_count_remains_bounded(self):
  b=io.BytesIO()
  with zipfile.ZipFile(b,'w') as z:
   z.writestr('ppt/presentation.xml','<presentation/>')
   for i in range(513):z.writestr('ppt/media/item'+str(i)+'.dat','x')
  with self.assertRaises(APIError) as raised:self.service()._parse('.pptx',b.getvalue())
  self.assertEqual(raised.exception.code,'DOCUMENT_TOO_COMPLEX')

class LargeOcrImageTests(DocumentImportTests):
 def test_large_handover_image_is_downsampled_but_original_and_slide_provenance_are_retained(self):
  from PIL import Image
  from unittest.mock import patch
  image=io.BytesIO();Image.new('RGB',(7200,7200),'white').save(image,format='JPEG')
  b=io.BytesIO()
  with zipfile.ZipFile(b,'w') as z:
   z.writestr('ppt/presentation.xml','<presentation/>');z.writestr('ppt/slides/slide1.xml','<slide/>')
   z.writestr('ppt/slides/_rels/slide1.xml.rels','<Relationships><Relationship Id="img" Target="../media/image1.jpeg"/></Relationships>')
   z.writestr('ppt/media/image1.jpeg',image.getvalue())
  s=self.service()
  with patch.object(s,'_ocr',return_value=['large-image text']):d=s.create({'operationId':'large-image','filename':'big.pptx','contentBase64':base64.b64encode(b.getvalue()).decode()})
  block=d['blocks'][0];self.assertEqual(block.get('ocrTransform',{}).get('originalWidth'),7200)
  self.assertEqual(block['sourceSlides'][0]['slideNumber'],1);self.assertTrue(any('缩小' in i for i in d['issues']))
  self.assertEqual((s.directory/(d['id']+'.pptx')).read_bytes(),b.getvalue())
 def test_ocr_decode_rejects_source_pixel_bomb_before_engine(self):
  from PIL import Image
  from unittest.mock import patch
  s=self.service()
  with patch('PIL.Image.open') as open_image:
   im=open_image.return_value.__enter__.return_value;im.width=100000;im.height=100000
   with self.assertRaises(APIError) as raised:s._ocr(b'header')
  self.assertEqual(raised.exception.code,'IMAGE_TOO_LARGE')

class InheritedPptProvenanceTests(DocumentImportTests):
 def test_inherited_layout_and_master_images_trace_back_to_source_slide(self):
  b=io.BytesIO()
  with zipfile.ZipFile(b,'w') as z:
   z.writestr('ppt/presentation.xml','<presentation/>');z.writestr('ppt/slides/slide7.xml','<slide/>')
   z.writestr('ppt/slides/_rels/slide7.xml.rels','<Relationships><Relationship Id="layout" Target="../slideLayouts/layout.xml"/></Relationships>')
   z.writestr('ppt/slideLayouts/layout.xml','<layout/>');z.writestr('ppt/slideLayouts/_rels/layout.xml.rels','<Relationships><Relationship Id="master" Target="../slideMasters/master.xml"/></Relationships>')
   z.writestr('ppt/slideMasters/master.xml','<master/>');z.writestr('ppt/slideMasters/_rels/master.xml.rels','<Relationships><Relationship Id="logo" Target="../media/logo.png"/></Relationships>')
   z.writestr('ppt/media/logo.png','placeholder')
  with zipfile.ZipFile(io.BytesIO(b.getvalue())) as z:sources,numbers=self.service()._ppt_sources(z,z.namelist())
  source=sources.get('ppt/media/logo.png',[{}])[0]
  self.assertEqual(source.get('slideNumber'),1);self.assertEqual(source.get('sourceKind'),'inherited_layout_or_master');self.assertEqual(source.get('relationshipChain'),['layout','master','logo'])

class ChunkMergeTests(DocumentImportTests):
 def fixture_blocks(self,s,operation='chunk-upload'):
  d=s.create({'operationId':operation,'filename':'x.docx','contentBase64':base64.b64encode(docx_bytes()).decode()})
  with self.store.transaction() as rt:
   d['blocks']=[{'id':'b'+str(i),'location':'slide '+str(i),'text':'evidence'} for i in range(129)];rt.db.execute('UPDATE document_imports SET payload=? WHERE id=?',(json.dumps(d),d['id']))
  return d
 def test_chunk_conflicts_remain_unresolved_with_both_source_claims(self):
  calls=[]
  def brain(path,payload):
   calls.append(payload);index=len(calls)
   return {'draft':{'name':'chip','qubits':[{'id':'Q8','physicalId':'physical8','busId':'B1','parameters':{'drive_hz':4.8e9 if index==1 else 4.9e9},'provenance':{'drive_hz':{'kind':'document','location':payload['blocks'][0]['location'],'blockId':payload['blocks'][0]['id']}}}]},'issues':[]}
  s=self.service(brain);d=self.fixture_blocks(s);result=s.analyze(d['id'],{'operationId':'chunks','expectedRevision':1})
  q=result['draft']['qubits'][0];self.assertIsNone(q['parameters']['drive_hz']);self.assertEqual(q['identityStatus'],'unresolved')
  self.assertEqual(len(q['provenance']['drive_hz']['evidence']),2);self.assertTrue(any('冲突' in issue for issue in result['issues']))
  self.assertEqual(q['conflicts'][0]['field'],'parameters.drive_hz');self.assertEqual(q['conflicts'][0]['values'],[4.8e9,4.9e9])
 def test_second_chunk_malformed_or_failed_leaves_original_draft_and_never_retries_same_operation(self):
  for failure in ('malformed','failed'):
   calls=[]
   def brain(path,payload):
    calls.append(payload)
    if len(calls)==2:
     if failure=='failed':raise APIError('MODEL_UNAVAILABLE','offline',503)
     return {'message':'malformed extraction'}
    return {'draft':{'name':'partial must not publish','qubits':[]}}
   s=self.service(brain);d=self.fixture_blocks(s,'chunk-upload-'+failure);body={'operationId':'chunk-'+failure,'expectedRevision':1}
   for attempt in range(2):
    with self.assertRaises(APIError):s.analyze(d['id'],body)
   self.assertEqual(len(calls),2);self.assertEqual(s.get(d['id'])['revision'],1);self.assertEqual(s.get(d['id'])['draft']['name'],'x')
   with self.store.connect() as rt:
    count=rt.db.execute('SELECT count(*) FROM document_import_chunks WHERE operation_id=?',(body['operationId'],)).fetchone()[0]
   self.assertEqual(count,2 if failure=='malformed' else 1)
 def test_explicit_physical_match_merges_but_equal_frequency_never_merges_ids(self):
  calls=[]
  def brain(path,payload):
   calls.append(payload)
   rows=[{'id':'A','physicalId':'physical-A','parameters':{'drive_hz':4.8e9}}] if len(calls)==1 else [{'physicalId':'physical-A','parameters':{'readout_hz':7.1e9}},{'id':'B','physicalId':'physical-B','parameters':{'drive_hz':4.8e9}}]
   return {'draft':{'name':'chip','qubits':rows}}
  s=self.service(brain);d=self.fixture_blocks(s);result=s.analyze(d['id'],{'operationId':'physical-match','expectedRevision':1});qs={q['id']:q for q in result['draft']['qubits']}
  self.assertEqual(set(qs),{'A','B'});self.assertEqual(qs['A']['parameters']['readout_hz'],7.1e9);self.assertEqual(qs['B']['parameters']['drive_hz'],4.8e9)
  self.assertTrue(qs['B']['unresolvedFields']);self.assertEqual(qs['A']['identityStatus'],'unresolved')

class ManageableModelChunkTests(DocumentImportTests):
 def test_extraction_chunks_have_small_reasoning_workload_and_preserve_every_block(self):
  s=self.service();d={'id':'doc','draft':{'name':'deck','qubits':[]},'blocks':[{'id':'b'+str(i),'location':'slide '+str(i//40),'text':'drive_hz explicit evidence; plot axis tick is not configuration'} for i in range(1000)]}
  chunks=s._model_chunks(d)
  self.assertGreater(len(chunks),1)
  self.assertEqual([b['id'] for c in chunks for b in c['blocks']],[b['id'] for b in d['blocks']])
  from backend.common import dumps
  for chunk in chunks:
   self.assertLessEqual(len(chunk['blocks']),128);self.assertLessEqual(len(dumps(chunk).encode('utf-16-le'))//2,40000)
   self.assertIn('axis ticks',chunk['instructions']);self.assertIn('empty qubits',chunk['instructions'])
 def test_waiting_model_surfaces_exact_output_exhaustion_error_without_generic_json_failure(self):
  with self.assertRaises(APIError) as raised:self.service()._structure_result({'status':'waiting_model','error':{'code':'provider_output_limit_without_text'},'message':'No final answer'})
  self.assertEqual(raised.exception.code,'provider_output_limit_without_text');self.assertEqual(raised.exception.status,503);self.assertIn('输出预算',raised.exception.message)

class ModelIssueDisplayTests(DocumentImportTests):
 def test_issue_objects_render_readable_text_and_human_evidence_locations_without_dict_repr(self):
  def brain(path,payload):return {'draft':{'name':'chip','qubits':[]},'issues':[{'issue':'Physical identity is unresolved','evidence':[payload['blocks'][0]['id']]},{'message':'请核对读出频率','evidence':[{'blockId':payload['blocks'][0]['id']}]},{'other':{'nested':'retained only as data'}}]}
  s=self.service(brain);d=s.create({'operationId':'issue-upload','filename':'x.docx','contentBase64':base64.b64encode(docx_bytes()).decode()});r=s.analyze(d['id'],{'operationId':'issue-analyze','expectedRevision':1})
  self.assertFalse(any('{' in issue or "'evidence'" in issue for issue in r['issues']))
  self.assertTrue(any('word/document.xml paragraph 1' in issue['text'] for issue in r['analysisSummary']['modelIssues']))
  self.assertTrue(any('请核对读出频率' in issue['text'] for issue in r['analysisSummary']['modelIssues']))
  self.assertEqual(r['analysisSummary']['issueRecords'][0]['issue']['issue'],'Physical identity is unresolved')

class TableStructureTests(DocumentImportTests):
 def test_actual_handover_slide28_rows_repeat_exact_headers_without_shift(self):
  b=io.BytesIO();xml=_local_fixture(self,'handover-slide28.xml').read_bytes()
  with zipfile.ZipFile(b,'w') as z:z.writestr('ppt/presentation.xml','<presentation/>');z.writestr('ppt/slides/slide28.xml',xml)
  blocks,_=self.service()._parse('.pptx',b.getvalue())
  rows=[row for row in blocks if row.get('kind')=='table_row'];self.assertTrue(rows,'Actual handover table has been flattened into unrelated paragraphs')
  q1=next(row for row in rows if row['table']['cells'][0]['text']=='Q1')
  cells=q1['table']['cells'];self.assertEqual((cells[5]['header'],cells[5]['text']),('fQ (GHz)','4.391'))
  self.assertEqual((cells[6]['header'],cells[6]['text']),('fQ_design (GHz)','4.5'))
  self.assertEqual((cells[9]['header'],cells[9]['text']),('|0>_fid','0.6378'))
  self.assertEqual((cells[12]['header'],cells[12]['text']),('T2* (us)','10.13'))
  self.assertIn('R2C6',q1['text']);self.assertIn('fQ (GHz)',q1['text']);self.assertIn('4.391',q1['text'])
  self.assertFalse(any(row.get('kind')!='table_row' and row['text']=='4.391' for row in blocks))
  d={'id':'real','draft':{'name':'real'},'blocks':blocks};chunks=self.service()._model_chunks(d)
  sent=next(row for chunk in chunks for row in chunk['blocks'] if row['id']==q1['id']);self.assertEqual(sent['text'],q1['text'])
 def test_docx_table_keeps_empty_and_merged_cell_column_coordinates(self):
  xml='<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Introduction</w:t></w:r></w:p><w:tbl><w:tblGrid><w:gridCol/><w:gridCol/><w:gridCol/></w:tblGrid><w:tr>'+''.join('<w:tc><w:p><w:r><w:t>'+v+'</w:t></w:r></w:p></w:tc>' for v in ['qubit','drive (GHz)','readout (GHz)'])+'</w:tr><w:tr>'+''.join('<w:tc><w:p><w:r><w:t>'+v+'</w:t></w:r></w:p></w:tc>' for v in ['Q1','','7.1'])+'</w:tr><w:tr><w:tc><w:tcPr><w:gridSpan w:val="2"/></w:tcPr><w:p><w:r><w:t>merged label</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>7.2</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>'
  b=io.BytesIO()
  with zipfile.ZipFile(b,'w') as z:z.writestr('word/document.xml',xml)
  blocks,_=self.service()._parse('.docx',b.getvalue());rows=[b for b in blocks if b.get('kind')=='table_row']
  self.assertEqual(len(rows),2);self.assertEqual(rows[0]['table']['cells'][1]['text'],'');self.assertEqual(rows[0]['table']['cells'][2]['coordinate'],'R2C3')
  self.assertTrue(rows[1]['table']['cells'][1]['mergeContinuation']);self.assertEqual(rows[1]['table']['cells'][2]['text'],'7.2')
  self.assertTrue(rows[1]['table']['uncertainty']);self.assertEqual(sum(b['text']=='Introduction' for b in blocks),1)

class HistoricalTableSafetyTests(DocumentImportTests):
 def document(self,s):
  b=io.BytesIO()
  with zipfile.ZipFile(b,'w') as z:z.writestr('ppt/presentation.xml','<presentation/>');z.writestr('ppt/slides/slide28.xml',_local_fixture(self,'handover-slide28.xml').read_bytes())
  return s.create({'operationId':'table-upload','filename':'table.pptx','contentBase64':base64.b64encode(b.getvalue()).decode()})
 def test_historical_metrics_stay_out_of_parameters_and_readout_definition_requires_choice(self):
  row_id=[None]
  def brain(path,payload):return {'draft':{'name':'table','qubits':[{'id':'Q1','parameters':{'drive_hz':4.391e9,'readout_hz':7.08805e9,'fQ_design':4.5,'T2star':-109,'fid0':-12.02},'provenance':{'drive_hz':{'blockId':row_id[0]},'readout_hz':{'blockId':row_id[0]}}}]}}
  s=self.service(brain);d=self.document(s);row_id[0]=next(b['id'] for b in d['blocks'] if b.get('kind')=='table_row' and b['table']['cells'][0]['text']=='Q1')
  result=s.analyze(d['id'],{'operationId':'safe-table','expectedRevision':1});q=result['draft']['qubits'][0]
  self.assertEqual(q['parameters']['drive_hz'],4.391e9);self.assertIsNone(q['parameters'].get('readout_hz'))
  self.assertFalse({'fQ_design','T2star','fid0'}&set(q['parameters']));self.assertEqual(len(q['provenance']['readout_hz']['alternatives']),2)
  self.assertEqual(len(result['analysisSummary']['historicalClaims']),3);self.assertTrue(any('定义' in i for i in result['issues']))
 def test_wrong_drive_claim_from_design_column_is_rejected_against_actual_row(self):
  row_id=[None]
  def brain(path,payload):return {'draft':{'qubits':[{'id':'Q1','parameters':{'drive_hz':4.5e9},'provenance':{'drive_hz':{'blockId':row_id[0]}}}]}}
  s=self.service(brain);d=self.document(s);row_id[0]=next(b['id'] for b in d['blocks'] if b.get('kind')=='table_row' and b['table']['cells'][0]['text']=='Q1')
  result=s.analyze(d['id'],{'operationId':'reject-design','expectedRevision':1});q=result['draft']['qubits'][0]
  self.assertIsNone(q['parameters'].get('drive_hz'));self.assertTrue(any('原始表格' in i for i in result['issues']))
 def test_xlsx_data_rows_repeat_headers_and_keep_blank_cells(self):
  import backend.document_imports,openpyxl
  workbook=openpyxl.Workbook();ws=workbook.active;ws.append(['qubit','drive (GHz)','readout (GHz)']);ws.append(['Q1',None,7.1]);raw=io.BytesIO();workbook.save(raw)
  blocks,_=self.service()._parse('.xlsx',raw.getvalue());row=next(b for b in blocks if b.get('kind')=='table_row')
  self.assertEqual(row['table']['cells'][1]['coordinate'],'B2');self.assertEqual(row['table']['cells'][1]['text'],'');self.assertIn('B2 [drive (GHz)]=[空]',row['text']);self.assertIn('C2 [readout (GHz)]=7.1',row['text'])
 def test_docx_omitted_leading_grid_cells_do_not_shift_columns(self):
  xml='<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:tbl><w:tblGrid><w:gridCol/><w:gridCol/><w:gridCol/></w:tblGrid><w:tr>'+''.join('<w:tc><w:p><w:r><w:t>'+v+'</w:t></w:r></w:p></w:tc>' for v in ['qubit','drive (GHz)','readout (GHz)'])+'</w:tr><w:tr><w:trPr><w:gridBefore w:val="1"/></w:trPr>'+''.join('<w:tc><w:p><w:r><w:t>'+v+'</w:t></w:r></w:p></w:tc>' for v in ['4.8','7.1'])+'</w:tr></w:tbl></w:body></w:document>'
  rows=self.service()._office_text_blocks(xml,'word/document.xml');row=next(r for r in rows if r.get('kind')=='table_row')
  self.assertEqual(row['table']['cells'][0]['text'],'');self.assertEqual(row['table']['cells'][1]['text'],'4.8');self.assertEqual(row['table']['cells'][1]['header'],'drive (GHz)')
 def test_model_null_readout_retains_actual_frequency_alternatives(self):
  row_id=[None]
  def brain(path,payload):return {'draft':{'qubits':[{'id':'Q1','parameters':{'drive_hz':4.391e9,'readout_hz':None},'provenance':{'drive_hz':{'blockId':row_id[0]},'readout_hz':{'blockId':row_id[0]}}}]}}
  s=self.service(brain);d=self.document(s);row_id[0]=next(b['id'] for b in d['blocks'] if b.get('kind')=='table_row' and b['table']['cells'][0]['text']=='Q1')
  result=s.analyze(d['id'],{'operationId':'null-readout','expectedRevision':1});q=result['draft']['qubits'][0]
  self.assertIsNone(q['parameters']['readout_hz']);self.assertEqual(q['parameters']['drive_hz'],4.391e9)
  alternatives=q['provenance']['readout_hz']['alternatives']
  self.assertEqual([a['value'] for a in alternatives],[7.08805e9,7.08798e9])
  self.assertEqual([a['coordinate'] for a in alternatives],['R2C2','R2C3'])
 def test_table_without_explicit_frequency_units_cannot_validate_model_frequency(self):
  row={'id':'unknown-unit-row','kind':'table_row','location':'table row 2','table':{'uncertainty':[],'cells':[{'text':'Q1','header':'qubit','coordinate':'R2C1'},{'text':'4.391','header':'frequency','coordinate':'R2C2'}]}}
  clean,_,concerns=self.service()._initial_claims({'id':'Q1','parameters':{'drive_hz':4.391e9},'provenance':{'drive_hz':{'blockId':row['id']}}},{row['id']:row})
  self.assertIsNone(clean['drive_hz']);self.assertTrue(any('单位' in c['reason'] for c in concerns))
 def test_xlsx_merged_headers_and_continuations_remain_explicitly_ambiguous(self):
  import backend.document_imports,openpyxl
  workbook=openpyxl.Workbook();ws=workbook.active
  ws.append(['qubit','drive (GHz)','']);ws.merge_cells('B1:C1');ws.append(['Q1',4.391,None]);ws.merge_cells('B2:C2')
  raw=io.BytesIO();workbook.save(raw);blocks,_=self.service()._parse('.xlsx',raw.getvalue());row=next(b for b in blocks if b.get('kind')=='table_row')
  self.assertTrue(row['table']['cells'][2]['mergeContinuation']);self.assertEqual(row['table']['cells'][2]['mergeOrigin'],'B2')
  self.assertTrue(any('表头含合并' in u for u in row['table']['uncertainty']))
  clean,_,concerns=self.service()._initial_claims({'id':'Q1','parameters':{'drive_hz':4.391e9},'provenance':{'drive_hz':{'blockId':row['id']}}},{row['id']:row})
  self.assertIsNone(clean['drive_hz']);self.assertTrue(concerns)
 def test_empty_model_preserves_literal_qubit_label_and_measured_frequency_from_real_table(self):
  s=self.service(lambda *_:{'draft':{'qubits':[]},'issues':[]});d=self.document(s)
  result=s.analyze(d['id'],{'operationId':'explicit-table-fallback','expectedRevision':1})
  q=next(q for q in result['draft']['qubits'] if q['id']=='Q1')
  self.assertEqual(q['parameters']['drive_hz'],4.391e9)
  self.assertIsNone(q['parameters']['readout_hz']);self.assertEqual(q['identityStatus'],'unresolved');self.assertIsNone(q.get('physicalId'));self.assertIsNone(q.get('busId'))
  source=q['provenance']['drive_hz'];self.assertEqual(source['kind'],'document');self.assertEqual(source['coordinate'],'R2C6');self.assertEqual(source['header'],'fQ (GHz)');self.assertEqual(source['rawValue'],'4.391');self.assertFalse(source['acceptedCalibration'])
  self.assertEqual([v['value'] for v in q['provenance']['readout_hz']['alternatives']],[7.08805e9,7.08798e9])
  self.assertFalse({'T1','T2star','fidelity'}&set(q['parameters']))
 def test_explicit_table_fallback_does_not_infer_missing_units_design_values_or_merged_identity(self):
  from copy import deepcopy
  b=io.BytesIO()
  with zipfile.ZipFile(b,'w') as z:z.writestr('ppt/presentation.xml','<presentation/>');z.writestr('ppt/slides/slide28.xml',_local_fixture(self,'handover-slide28.xml').read_bytes())
  rows,_=self.service()._parse('.pptx',b.getvalue());q1=next(row for row in rows if row.get('kind')=='table_row' and row['table']['cells'][0]['text']=='Q1')
  for changed in ('unit','design','merged_header','merged_identity','merged_value'):
   row=deepcopy(q1)
   if changed=='unit':row['table']['cells'][5]['header']='fQ'
   elif changed=='design':row['table']['cells'][5]['header']='fQ_design (GHz)'
   elif changed=='merged_header':row['table']['uncertainty'].append('表头含合并单元格')
   elif changed=='merged_identity':row['table']['cells'][0]['mergeContinuation']=True
   else:row['table']['cells'][5]['mergeContinuation']=True
   with self.subTest(changed=changed):self.assertEqual(self.service()._explicit_table_claims([row]),[])
 def test_explicit_table_evidence_and_conflicting_model_claim_remain_unresolved(self):
  s=self.service(lambda *_:{'draft':{'qubits':[{'id':'Q1','parameters':{'drive_hz':4.9e9},'provenance':{'drive_hz':{'kind':'inferred','location':'model estimate'}}}]}});d=self.document(s)
  result=s.analyze(d['id'],{'operationId':'explicit-conflict','expectedRevision':1});q=next(q for q in result['draft']['qubits'] if q['id']=='Q1')
  self.assertIsNone(q['parameters']['drive_hz']);self.assertEqual(q['conflicts'][0]['values'],[4.391e9,4.9e9])
  self.assertEqual(q['provenance']['drive_hz']['evidence'][0]['coordinate'],'R2C6')


class GenuineDeliveryTests(DocumentImportTests):
 def test_genuine_delivery_two_header_rows_and_serial_identity_column(self):
  xml=_local_fixture(self,'genuine-delivery-document.xml').read_bytes()
  rows=self.service()._office_text_blocks(xml,'word/document.xml')
  for i,row in enumerate(rows):row['id']='genuine-'+str(i)
  design=[r for r in rows if r.get('kind')=='table_row' and r['table']['tableIndex']==5]
  self.assertEqual(len(design),19);self.assertEqual(design[0]['table']['cells'][2]['header'],'比特频率 (GHz)')
  claims=self.service()._explicit_table_claims(rows);self.assertEqual(len(claims),13)
  q11=next(q for q in claims if q['id']=='Q11');self.assertEqual(q11['parameters']['drive_hz'],2.14e9);self.assertEqual(q11['parameters']['readout_hz'],6.89e9)
  self.assertEqual(q11['provenance']['drive_hz']['role'],'design_initialization_only');self.assertFalse(q11['provenance']['drive_hz']['acceptedCalibration'])
  self.assertEqual(q11['identityStatus'],'unresolved');self.assertIsNone(q11['physicalId'])

class ParserReviewRegressions(DocumentImportTests):
 def row(self,headers=('序号','Qubit ID','drive (GHz)'),labels=('1','Q11','4.8')):
  return {'id':'row','kind':'table_row','location':'table row 2','table':{'sourceRole':'design','cells':[{'header':h,'text':v,'coordinate':'R2C'+str(i+1),'gridSpan':1} for i,(h,v) in enumerate(zip(headers,labels))],'uncertainty':[]}}
 def test_normalized_identity_rejects_wrong_model_qubit_and_ambiguous_headers(self):
  s=self.service();row=self.row();claim={'id':'Q18','parameters':{'drive_hz':4.8e9},'provenance':{'drive_hz':{'blockId':'row'}}}
  params,_,concerns=s._initial_claims(claim,{'row':row});self.assertIsNone(params['drive_hz']);self.assertTrue(concerns)
  self.assertEqual(s._explicit_table_claims([row])[0]['id'],'Q11')
  row=self.row(('Qubit ID','Qubit Name','drive (GHz)'),('Q11','Q18','4.8'))
  self.assertEqual(s._explicit_table_claims([row]),[])
  params,_,concerns=s._initial_claims({'id':'Q11',**{k:v for k,v in claim.items() if k!='id'}},{'row':row});self.assertIsNone(params['drive_hz']);self.assertTrue(concerns)
 def test_nonfinite_design_values_are_null_with_issue_and_create_remains_serializable(self):
  from backend.common import dumps
  s=self.service()
  for text in ('NaN','Infinity','1e999'):
   row=self.row(('序号','比特名称','比特频率 (GHz)'),('1','Q11',text));resources=s._design_resources([row])
   self.assertIsNone(resources[0]['design_drive_hz']);self.assertTrue(resources[0]['issues']);dumps(resources)
   xml='<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>设计参数</w:t></w:r></w:p><w:tbl><w:tr>'+''.join('<w:tc><w:p><w:r><w:t>'+v+'</w:t></w:r></w:p></w:tc>' for v in ('序号','比特名称','比特频率 (GHz)'))+'</w:tr><w:tr>'+''.join('<w:tc><w:p><w:r><w:t>'+v+'</w:t></w:r></w:p></w:tc>' for v in ('1','Q11',text))+'</w:tr></w:tbl></w:body></w:document>'
   raw=io.BytesIO()
   with zipfile.ZipFile(raw,'w') as z:z.writestr('word/document.xml',xml)
   result=s.create({'operationId':'nonfinite-'+text,'filename':'nonfinite.docx','contentBase64':base64.b64encode(raw.getvalue()).decode()})
   self.assertEqual(result['status'],'parsed');self.assertIsNone(result['draft']['designResources'][0]['design_drive_hz']);self.assertTrue(any('有限' in issue for issue in result['issues']));dumps(result)

class ActualModelResourceTypingTests(DocumentImportTests):
 def test_real_model_resonator_claims_do_not_become_qubits_and_all_design_values_survive(self):
  claims=json.loads(_local_fixture(self,'actual-model-design-resource-claims.json').read_text(encoding='utf-8'))['qubits']
  s=self.service(lambda *_:{'draft':{'qubits':claims},'issues':[]});xml=_local_fixture(self,'genuine-delivery-document.xml').read_bytes();raw=io.BytesIO()
  with zipfile.ZipFile(raw,'w') as z:z.writestr('word/document.xml',xml)
  d=s.create({'operationId':'real-model-resource-upload','filename':'genuine.docx','contentBase64':base64.b64encode(raw.getvalue()).decode()});result=s.analyze(d['id'],{'operationId':'real-model-resource-analyze','expectedRevision':1})
  qs={q['id']:q for q in result['draft']['qubits']};self.assertEqual(set(qs),{'Q'+str(i) for i in range(1,14)})
  self.assertEqual(len(result['draft']['designResources']),19);self.assertEqual(sum(r['kind']=='resonator' for r in result['draft']['designResources']),6)
  for original in claims:
   if original['id'].startswith('Q'):self.assertEqual(qs[original['id']]['parameters'],original['parameters'])
  self.assertEqual(qs['Q11']['parameters'],{'drive_hz':2140000000.0,'readout_hz':6890000000.0})
  self.assertEqual({q['id'] for q in result['analysisSummary']['excludedNonQubitClaims']},{'R'+str(i) for i in range(1,7)})
  self.assertTrue(all(q['identityStatus']=='unresolved' for q in qs.values()))
 def test_typing_filter_uses_document_evidence_not_resource_name_prefix(self):
  s=self.service();self.assertIsNone(s._non_qubit_design_reference({'id':'R1','parameters':{'drive_hz':4.8e9}}, {}, {}))
  registry={'R1':{'id':'R1','kind':'resonator','provenance':{'blockId':'resonator-row'}}}
  self.assertEqual(s._non_qubit_design_reference({'id':'Q99','provenance':{'drive_hz':{'blockId':'resonator-row'}}},registry,{}),'R1')
 def test_free_model_narrative_cannot_contradict_final_design_seed_state_or_flood_user_issues(self):
  fixture=json.loads(_local_fixture(self,'actual-model-design-resource-claims.json').read_text(encoding='utf-8'))
  fixture['issues']=['表5为设计仿真参数表，仅含设计值，无实测比特频率/读取频率配置；drive_hz与readout_hz均置null。','表5的设计频率未作为执行参数写入。','芯片名称与currentDraft一致。','物理映射仍有不确定性，请核对来源。']
  s=self.service(lambda *_:{'draft':{'qubits':fixture['qubits']},'issues':fixture['issues']});raw=io.BytesIO()
  with zipfile.ZipFile(raw,'w') as z:z.writestr('word/document.xml',_local_fixture(self,'genuine-delivery-document.xml').read_bytes())
  d=s.create({'operationId':'narrative-upload','filename':'genuine.docx','contentBase64':base64.b64encode(raw.getvalue()).decode()});r=s.analyze(d['id'],{'operationId':'narrative-analyze','expectedRevision':1})
  q11=next(q for q in r['draft']['qubits'] if q['id']=='Q11');self.assertEqual(q11['parameters']['drive_hz'],2140000000.0)
  self.assertFalse(any('均置null' in i or '未作为执行参数' in i or 'currentDraft' in i for i in r['issues']))
  self.assertTrue(any('未验收初值' in i for i in r['issues']));self.assertLessEqual(len(r['issues']),10)
  self.assertTrue(any('均置null' in i['text'] for i in r['analysisSummary']['modelIssues']));self.assertTrue(r['analysisSummary']['modelIssuesRequireReview'])
  self.assertTrue(any('待核对' in i for i in r['issues']));self.assertTrue(any('物理身份' in i for i in r['issues']))
