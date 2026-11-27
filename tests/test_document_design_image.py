"""Normal import/save contracts; synthetic DOCX fixtures never assert chip identity."""
import base64
import copy
import hashlib
import io
import json
import struct
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path
from unittest.mock import patch

from backend.chip_catalog import ChipCatalog
from backend.common import APIError, dumps
from backend.document_imports import DocumentImports
from backend.store import Store

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
WP = 'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing'
PKG = 'http://schemas.openxmlformats.org/package/2006/relationships'


def png(width=2, height=2, payload=None):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    rows = payload if payload is not None else (b'\x00' + b'\x40\x80\xc0' * width) * height
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))


def para(text):
    return '<w:p><w:r><w:t>' + text + '</w:t></w:r></w:p>'


def picture(ref='r1', attribute='embed'):
    return '<w:p><w:r><w:drawing><wp:inline><a:blip r:' + attribute + '="' + ref + '"/></wp:inline></w:drawing></w:r></w:p>'


def docx(body=None, relations=None, media=None):
    if body is None: body = picture() + para('图1 芯片设计版图')
    if relations is None: relations = '<Relationship Id="r1" Type="' + R + '/image" Target="media/layout.png"/>'
    if media is None: media = {'word/media/layout.png': png()}
    result = io.BytesIO()
    with zipfile.ZipFile(result, 'w') as archive:
        archive.writestr('[Content_Types].xml', '<Types/>')
        archive.writestr('word/document.xml', '<w:document xmlns:w="' + W + '" xmlns:a="' + A + '" xmlns:r="' + R + '" xmlns:wp="' + WP + '"><w:body>' + body + '</w:body></w:document>')
        archive.writestr('word/_rels/document.xml.rels', '<Relationships xmlns="' + PKG + '">' + relations + '</Relationships>')
        for name, raw in media.items(): archive.writestr(name, raw)
    return result.getvalue()


class DocumentDesignImageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'data/test.db', root=self.root)
        self.catalog = ChipCatalog(self.store, self.root, {})
        self.service = DocumentImports(self.store, self.root, self.catalog, lambda *_: {'draft': {'name': '新芯片', 'qubits': []}})
        self.service._ocr = lambda _: []
        self.count = 0

    def tearDown(self):
        self.temp.cleanup()

    def create(self, raw=None, filename='另一片.docx'):
        self.count += 1
        return self.service.create({'operationId': 'upload-' + str(self.count), 'filename': filename,
                                    'contentBase64': base64.b64encode(raw if raw is not None else docx()).decode()})

    def test_normal_import_contains_exact_embedded_png_and_source_without_topology_claims(self):
        raw = docx()
        record = self.create(raw)
        diagram = record['draft']['topology']
        image = diagram['image']
        self.assertEqual(base64.b64decode(image['dataUrl'].split(',', 1)[1]), png())
        self.assertEqual((image['width'], image['height']), (2, 2))
        for source in (image['source'], diagram['source']):
            for literal in ('另一片.docx', record['id'], hashlib.sha256(raw).hexdigest(), 'word/media/layout.png', '图1 芯片设计版图', hashlib.sha256(png()).hexdigest()):
                self.assertIn(literal, source)
        self.assertEqual(set(diagram), {'source', 'image', 'nodes', 'edges'})
        self.assertEqual(diagram['nodes'], [])
        self.assertEqual(diagram['edges'], [])
        self.assertEqual(record['draft']['qubits'], [])
        self.assertNotIn('routes', record['draft'])

    def test_generic_caption_and_jpeg_work_for_another_filename(self):
        from PIL import Image
        output = io.BytesIO()
        Image.new('RGB', (3, 4), 'white').save(output, format='JPEG')
        raw = docx(para('图 2：芯片版图') + picture(),
                   '<Relationship Id="r1" Type="' + R + '/image" Target="media/another.jpeg"/>',
                   {'word/media/another.jpeg': output.getvalue()})
        record = self.create(raw, '与既有芯片无关.docx')
        image = record['draft']['topology']['image']
        self.assertTrue(image['dataUrl'].startswith('data:image/jpeg;base64,'))
        self.assertEqual((image['width'], image['height']), (3, 4))

    def test_analyze_update_restart_and_catalog_save_keep_the_same_image(self):
        record = self.create()
        expected = copy.deepcopy(record['draft']['topology'])
        for i in range(2):
            body = {'operationId': 'analyze-' + str(i), 'expectedRevision': record['revision']}
            record = self.service.analyze(record['id'], body)
            self.assertEqual(self.service.analyze(record['id'], body), record)
            self.assertEqual(record['draft']['topology'], expected)
            body = {'operationId': 'save-' + str(i), 'expectedRevision': record['revision'], 'draft': record['draft']}
            record = self.service.update(record['id'], body)
            self.assertEqual(self.service.update(record['id'], body), record)
        self.assertEqual(DocumentImports(self.store, self.root, self.catalog).get(record['id'])['draft']['topology'], expected)
        saved = self.catalog.save_chip({'operationId': 'save-chip', 'draft': record['draft']})
        self.assertEqual(saved['topology'], expected)
        self.assertEqual(saved['qubits'], [])

    def test_manual_image_survives_analysis_and_old_client_update(self):
        record = self.create()
        manual = copy.deepcopy(record['draft']['topology'])
        manual['image']['name'] = '人工已录入图.png'
        manual['image']['source'] = '现场提供的另一张设计图'
        manual['source'] = '人工录入'
        record['draft']['topology'] = manual
        record = self.service.update(record['id'], {'operationId': 'manual', 'expectedRevision': 1, 'draft': record['draft']})
        record = self.service.analyze(record['id'], {'operationId': 'analyze', 'expectedRevision': 2})
        record = self.service.update(record['id'], {'operationId': 'old-client', 'expectedRevision': 3, 'draft': {'name': '保留旧图', 'qubits': []}})
        self.assertEqual(record['draft']['topology'], manual)

    def test_explicit_manual_image_removal_is_not_undone_by_reanalysis(self):
        record = self.create()
        record['draft']['topology'] = {'source': '保留人工说明'}
        record = self.service.update(record['id'], {'operationId': 'remove', 'expectedRevision': 1, 'draft': record['draft']})
        record = self.service.analyze(record['id'], {'operationId': 'reanalyze', 'expectedRevision': 2})
        self.assertNotIn('image', record['draft']['topology'])

    def test_legacy_draft_without_topology_remains_editable_without_reopening_archive(self):
        record = self.create()
        record['draft'].pop('topology', None)
        with self.store.transaction() as rt:
            rt.db.execute('UPDATE document_imports SET payload=? WHERE id=?', (dumps(record), record['id']))
        (self.service.directory / (record['id'] + '.docx')).unlink()
        record = self.service.analyze(record['id'], {'operationId': 'old-analyze', 'expectedRevision': 1})
        self.assertNotIn('topology', record['draft'])

    def test_legacy_image_only_topology_normalizes_empty_lists_on_save_and_reanalysis(self):
        record = self.create()
        record['draft']['topology'].pop('nodes', None)
        record['draft']['topology'].pop('edges', None)
        with self.store.transaction() as rt:
            rt.db.execute('UPDATE document_imports SET payload=? WHERE id=?', (dumps(record), record['id']))
        record = self.service.analyze(record['id'], {'operationId': 'legacy-image-analyze', 'expectedRevision': 1})
        self.assertEqual(record['draft']['topology']['nodes'], [])
        self.assertEqual(record['draft']['topology']['edges'], [])
        record['draft']['topology'].pop('nodes')
        record['draft']['topology'].pop('edges')
        record = self.service.update(record['id'], {'operationId': 'legacy-image-save', 'expectedRevision': 2, 'draft': record['draft']})
        self.assertEqual(record['draft']['topology']['nodes'], [])
        self.assertEqual(record['draft']['topology']['edges'], [])

    def test_image_dimensions_use_existing_bounded_validator_without_pil_open(self):
        from backend.document_diagrams import docx_design_image
        with patch('PIL.Image.open', side_effect=AssertionError('No additional image decoder')):
            image, issue = docx_design_image(docx(), 'another.docx', 'import-id', 'a' * 64)
            self.assertIsNone(issue)
            self.assertEqual((image['width'], image['height']), (2, 2))
            image, issue = docx_design_image(docx(media={'word/media/layout.png': png(100000000, 1, b'\0')}), 'another.docx', 'import-id', 'a' * 64)
            self.assertIsNone(image)
            self.assertIn('原图', issue)

    def test_missing_external_ambiguous_and_noncaption_images_are_not_selected(self):
        cases = [
            docx(para('图1 芯片设计版图'), media={}),
            docx(picture() + para('图1 芯片封装照片')),
            docx(picture() + para('请参照图1芯片设计版图进行测量')),
            docx(picture() + para('图1 芯片设计版图') + picture('r2'),
                 '<Relationship Id="r1" Type="' + R + '/image" Target="media/layout.png"/><Relationship Id="r2" Type="' + R + '/image" Target="media/layout.png"/>'),
            docx(picture() + para('图1 芯片设计版图') + para('正文') + picture() + para('图2 芯片版图')),
            docx(picture(attribute='link') + para('图1 芯片设计版图'),
                 '<Relationship Id="r1" Type="' + R + '/image" Target="https://invalid.example/layout.png" TargetMode="External"/>', {}),
            docx(relations='<Relationship Id="r1" Type="' + R + '/image" Target="https://invalid.example/layout.png" TargetMode="External"/>', media={}),
            docx(picture() + para('这是另一段内容') + para('图1 芯片设计版图')),
        ]
        with patch('socket.socket.connect', side_effect=AssertionError('No network for document images')):
            for raw in cases:
                with self.subTest(index=cases.index(raw)):
                    self.assertNotIn('topology', self.create(raw)['draft'])

    def test_unsafe_relationships_duplicate_ids_and_multiple_blips_are_not_guessed(self):
        cases = [
            docx(relations='<Relationship Id="r1" Type="' + R + '/image" Target="../word/media/layout.png"/>'),
            docx(relations='<Relationship Id="r1" Type="' + R + '/image" Target="/word/media/layout.png"/>'),
            docx(relations='<Relationship Id="r1" Type="' + R + '/image" Target="media/layout.png"/><Relationship Id="r1" Type="' + R + '/image" Target="media/layout.png"/>'),
            docx(picture().replace('</w:drawing>', '<a:blip r:embed="r1"/></w:drawing>') + para('图1 芯片设计版图')),
            docx(relations='<Relationship Id="r1" Type="' + R + '/hyperlink" Target="media/layout.png"/>'),
        ]
        for raw in cases:
            with self.subTest(index=cases.index(raw)):
                self.assertNotIn('topology', self.create(raw)['draft'])

    def test_floating_or_revision_images_are_not_assumed_to_match_caption(self):
        for picture_xml in (picture().replace('wp:inline', 'wp:anchor'),
                            picture().replace('<w:r>', '<w:del><w:r>').replace('</w:r>', '</w:r></w:del>')):
            with self.subTest(picture=picture_xml):
                self.assertNotIn('topology', self.create(docx(picture_xml + para('图1 芯片版图')))['draft'])

    def test_same_paragraph_caption_and_blank_separation_are_supported(self):
        for body in (picture().replace('</w:p>', '<w:r><w:t>图1 芯片版图</w:t></w:r></w:p>'),
                     picture() + '<w:p/>' + para('图1 芯片版图')):
            self.assertIn('image', self.create(docx(body))['draft']['topology'])

    def test_existing_diagram_validator_enforces_size_pixels_and_content(self):
        # CRC damage leaves enough header for existing OCR-size parsing but fails the full diagram validator.
        invalid = bytearray(png()); invalid[-1] ^= 1
        from random import Random
        noise = Random(123).randbytes(600 * 400 * 3)
        large = png(600, 400, b''.join(b'\0' + noise[i:i + 1800] for i in range(0, len(noise), 1800)))
        for raw_image in (bytes(invalid), large, png(8193, 1)):
            with self.subTest(bytes=len(raw_image)):
                record = self.create(docx(media={'word/media/layout.png': raw_image}))
                self.assertNotIn('topology', record['draft'])
                self.assertTrue(any('原图' in str(issue) for issue in record['issues']))

    def test_existing_manual_invalid_image_is_rejected_instead_of_saved(self):
        record = self.create()
        record['draft']['topology']['image']['width'] = 999
        with self.assertRaises(APIError):
            self.service.update(record['id'], {'operationId': 'invalid-manual', 'expectedRevision': 1, 'draft': record['draft']})


if __name__ == '__main__':
    unittest.main()
