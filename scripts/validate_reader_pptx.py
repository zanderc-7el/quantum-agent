"""Check the produced package against its frozen evidence before offering download."""
import argparse
import hashlib
import json
import posixpath
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET


def verify_page_binding(package, number, slide, audit):
    ns = {'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
          'a': 'http://schemas.openxmlformats.org/drawingml/2006/main'}
    try:
        rels = ET.fromstring(package.read(f'ppt/slides/_rels/slide{number}.xml.rels'))
        notes_type = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide'
        targets = [r.get('Target', '') for r in rels if r.get('Type') == notes_type and r.get('TargetMode') != 'External']
        if len(targets) != 1:
            raise ValueError(f'Slide {number} must link to exactly one notes part')
        relative = targets[0]
        target = posixpath.normpath(relative[1:] if relative.startswith('/') else posixpath.join('ppt/slides', relative))
        if not target.startswith('ppt/notesSlides/'):
            raise ValueError(f'Slide {number} has an invalid notes relationship')
        notes = ET.fromstring(package.read(target))
        bodies = [shape for shape in notes.findall('.//p:sp', ns)
                  if shape.find("p:nvSpPr/p:nvPr/p:ph[@type='body']", ns) is not None]
        if len(bodies) != 1:
            raise ValueError(f'Slide {number} is missing its notes body')
        note = json.loads(''.join(text.text or '' for text in bodies[0].findall('.//a:t', ns)))
    except (KeyError, ET.ParseError, json.JSONDecodeError) as exc:
        raise ValueError(f'Slide {number} has unreadable evidence notes') from exc
    expected = {'workflowId': audit['workflowId'], 'revision': audit['revision'],
                'section': slide['section'], 'writingPolicy': audit['policyVersion'],
                'sourceSnapshotSha256': audit['sourceSnapshotSha256']}
    if slide['section'] == 'smart-gate-fidelity' and not slide.get('sources'):
        raise ValueError(f'Slide {number} has no gate evidence in its audit')
    if 'sources' in slide:
        expected['sources'] = slide['sources']
    if not isinstance(note, dict) or any(note.get(k) != v for k, v in expected.items()):
        raise ValueError(f'Slide {number} source binding differs')


def validate(build, expected_snapshot_sha256=None):
    build = Path(build)
    audit = json.loads((build / 'export-audit.json').read_text(encoding='utf-8'))
    output = build / 'report.pptx'
    snapshot_sha = audit.get('sourceSnapshotSha256')
    if expected_snapshot_sha256 is not None and expected_snapshot_sha256 != snapshot_sha:
        raise ValueError('Export audit does not match the frozen snapshot receipt')
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    if digest != audit['fileSha256']:
        raise ValueError('Delivered report hash differs')
    with ZipFile(output) as z:
        if z.testzip(): raise ValueError('Corrupt ZIP member')
        pages = [n for n in z.namelist() if n.startswith('ppt/slides/slide') and n.endswith('.xml')]
        if len(pages) != len(audit['slides']): raise ValueError('Slide count differs')
        media = {hashlib.sha256(z.read(n)).hexdigest() for n in z.namelist() if n.startswith('ppt/media/')}
        for number, slide in enumerate(audit['slides'], 1):
            if snapshot_sha:
                verify_page_binding(z, number, slide, audit)
            for image in slide['images']:
                if image['sha256'] not in media: raise ValueError('Source image bytes were not preserved')
        ns = {'a':'http://schemas.openxmlformats.org/drawingml/2006/main'}
        for number in audit['tablePages']:
            if not ET.fromstring(z.read(f'ppt/slides/slide{number}.xml')).findall('.//a:tbl',ns):
                raise ValueError('Editable table missing')
        for n in z.namelist():
            if n.endswith('.rels'):
                for rel in ET.fromstring(z.read(n)):
                    if rel.attrib.get('TargetMode') == 'External': raise ValueError('External package relation is not allowed')
    appendix = sum(len(s['images']) for s in audit['slides'] if s['section'] == 'appendix')
    if appendix != audit['sourceFigureCount']: raise ValueError('Not all source figures are in appendix')
    receipt = {'schema':'reader-pptx-validation/1','passed':True,'sha256':digest,'slides':len(pages),
               'sourceFigures':appendix,'nativeTables':len(audit['tablePages']),'humanAccepted':False,
               'scope':'Package, original image bytes, complete appendix and native tables. Human visual review is separate.'}
    if snapshot_sha:
        receipt.update(sourceSnapshotSha256=snapshot_sha, boundNotesPages=len(pages))
    (build / 'validation.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
    return receipt

if __name__ == '__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--build-dir',required=True)
    parser.add_argument('--snapshot-sha256')
    args=parser.parse_args()
    print(json.dumps(validate(args.build_dir, args.snapshot_sha256)))
