"""Check the produced package against its frozen evidence before offering download."""
import argparse
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET


def validate(build):
    build = Path(build)
    audit = json.loads((build / 'export-audit.json').read_text(encoding='utf-8'))
    output = build / 'report.pptx'
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    if digest != audit['fileSha256']:
        raise ValueError('Delivered report hash differs')
    with ZipFile(output) as z:
        if z.testzip(): raise ValueError('Corrupt ZIP member')
        pages = [n for n in z.namelist() if n.startswith('ppt/slides/slide') and n.endswith('.xml')]
        if len(pages) != len(audit['slides']): raise ValueError('Slide count differs')
        media = {hashlib.sha256(z.read(n)).hexdigest() for n in z.namelist() if n.startswith('ppt/media/')}
        for slide in audit['slides']:
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
    (build / 'validation.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
    return receipt

if __name__ == '__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--build-dir',required=True)
    print(json.dumps(validate(parser.parse_args().build_dir)))
