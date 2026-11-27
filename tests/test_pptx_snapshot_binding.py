import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from xml.etree.ElementTree import Element, SubElement, tostring
from zipfile import ZipFile

path = Path(__file__).resolve().parents[1] / 'scripts/validate_reader_pptx.py'
spec = importlib.util.spec_from_file_location('pptx_validation', path)
validation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validation)


class FrozenPptxNotes(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.build = Path(self.temp.name)
        self.digest = hashlib.sha256(b'{  "number": 1.00 }\n').hexdigest()
        self.sources = [{'role': 'selection', 'operationId': 'TEST_ONLY/measure'}]
        self.notes = {'workflowId': 'TEST_ONLY', 'revision': 12, 'section': 'smart-gate-fidelity',
                      'writingPolicy': 'reader-v1', 'sourceSnapshotSha256': self.digest,
                      'sources': self.sources}

    def write(self, notes, target='../notesSlides/notesSlide1.xml'):
        p = '{http://schemas.openxmlformats.org/presentationml/2006/main}'
        a = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
        root = Element(p+'notes')
        shape = SubElement(root,p+'sp')
        nv = SubElement(SubElement(shape,p+'nvSpPr'),p+'nvPr')
        SubElement(nv,p+'ph',{'type':'body'})
        SubElement(SubElement(shape,p+'txBody'),a+'t').text=json.dumps(notes)
        output = self.build/'report.pptx'
        with ZipFile(output,'w') as z:
            z.writestr('ppt/slides/slide1.xml',tostring(Element(p+'sld')))
            z.writestr('ppt/notesSlides/notesSlide1.xml',tostring(root))
            rels = Element('Relationships', xmlns='http://schemas.openxmlformats.org/package/2006/relationships')
            SubElement(rels,'Relationship',Id='rId1', Target=target,
                       Type='http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide')
            z.writestr('ppt/slides/_rels/slide1.xml.rels',tostring(rels))
        audit = {'fileSha256': hashlib.sha256(output.read_bytes()).hexdigest(),
                 'sourceSnapshotSha256': self.digest, 'workflowId':'TEST_ONLY', 'revision':12,
                 'policyVersion':'reader-v1','sourceFigureCount':0,'tablePages':[],
                 'slides':[{'section':'smart-gate-fidelity','sources':self.sources,'images':[]}]}
        (self.build/'export-audit.json').write_text(json.dumps(audit),encoding='utf-8')

    def test_actual_file_digest_matches_every_page(self):
        for target in ('../notesSlides/notesSlide1.xml', '/ppt/notesSlides/notesSlide1.xml'):
            with self.subTest(target=target):
                self.write(self.notes,target)
                result=validation.validate(self.build,self.digest)
                self.assertEqual(result['sourceSnapshotSha256'],self.digest)
                self.assertEqual(result['boundNotesPages'],1)

    def test_reencoded_or_wrong_receipt_hash_is_rejected(self):
        normalized=hashlib.sha256(b'{"number":1}').hexdigest()
        self.write(dict(self.notes,sourceSnapshotSha256=normalized))
        with self.assertRaises(ValueError):validation.validate(self.build,self.digest)
        self.write(self.notes)
        with self.assertRaises(ValueError):validation.validate(self.build,normalized)

    def test_gate_notes_must_keep_both_evidence_and_base_metadata(self):
        for field in ('sources','section','revision','writingPolicy','workflowId','sourceSnapshotSha256'):
            with self.subTest(field=field):
                self.write({k:v for k,v in self.notes.items() if k!=field})
                with self.assertRaises(ValueError):validation.validate(self.build,self.digest)

    def test_page_must_link_to_the_verified_notes(self):
        self.write(self.notes,'../notesSlides/other-notes.xml')
        with self.assertRaises(ValueError):
            validation.validate(self.build,self.digest)

    def test_gate_page_cannot_drop_sources_from_both_note_and_audit(self):
        self.write({k:v for k,v in self.notes.items() if k!='sources'})
        audit_file=self.build/'export-audit.json'
        audit=json.loads(audit_file.read_text(encoding='utf-8'))
        del audit['slides'][0]['sources']
        audit_file.write_text(json.dumps(audit),encoding='utf-8')
        with self.assertRaises(ValueError):
            validation.validate(self.build,self.digest)

if __name__ == '__main__':
    unittest.main()
