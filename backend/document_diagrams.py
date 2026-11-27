"""Deterministic DOCX caption-to-media selection, never topology inference."""
import base64
import hashlib
import io
import re
import struct
import zipfile
import zlib
from pathlib import PurePosixPath
from xml.etree import ElementTree as ET

from .common import APIError
from .diagram_assets import MAX_IMAGE_BYTES, _jpeg_dimensions, _png_dimensions, validate_diagram_image

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
A = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
WP = '{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}'
PKG = '{http://schemas.openxmlformats.org/package/2006/relationships}'
CAPTION = re.compile(r'图\s*[0-9一二三四五六七八九十百]+(?:[.．\-－][0-9]+)*\s*[:：、.．]?\s*芯片(?:设计)?版图')


def _text(paragraph):
    return ''.join(node.text or '' for node in paragraph.iter(W + 't')).strip()


def _blank(paragraph):
    # Page/section breaks and embedded objects are boundaries, not empty space.
    return (paragraph.tag == W + 'p' and not _text(paragraph)
            and not any(node.tag in {W + tag for tag in ('drawing', 'pict', 'object', 'br', 'sectPr')}
                        for node in paragraph.iter()))


def docx_design_image(raw, filename, document_id, document_sha256):
    """Return one validated image and an optional readable issue.

    Runs only after the normal Office archive limits/parser have succeeded.
    Accepts one literal numbered caption in the main body, with one inline
    picture in that paragraph or its unambiguous adjacent picture paragraph.
    Complex layouts, repeated captions and external relationships need a human.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                return None, '文档存在重复包内路径，未自动带入设计原图，请人工选择。'
            body = ET.fromstring(archive.read('word/document.xml')).find(W + 'body')
            if body is None:
                return None, None
            paragraphs = list(body)
            captions = [(index, p, _text(p)) for index, p in enumerate(paragraphs)
                        if p.tag == W + 'p' and CAPTION.fullmatch(_text(p))]
            if not captions:
                return None, None
            if len(captions) != 1:
                return None, '文档有多个芯片版图题注，未自动选择原图，请人工核对。'
            index, caption_paragraph, caption = captions[0]
            pictures = []
            if list(caption_paragraph.iter(A + 'blip')):
                pictures.append(caption_paragraph)
            else:
                for direction in (-1, 1):
                    neighbor = index + direction
                    while 0 <= neighbor < len(paragraphs) and _blank(paragraphs[neighbor]):
                        neighbor += direction
                    if 0 <= neighbor < len(paragraphs):
                        p = paragraphs[neighbor]
                        if p.tag == W + 'p' and not _text(p) and list(p.iter(A + 'blip')):
                            pictures.append(p)
            if len(pictures) != 1:
                return None, '芯片版图题注无法唯一关联包内原图，请人工选择。'
            picture = pictures[0]
            blips = list(picture.iter(A + 'blip'))
            inline_blips = [blip for inline in picture.iter(WP + 'inline') for blip in inline.iter(A + 'blip')]
            uncertain = {W + name for name in ('pict', 'object', 'del', 'ins', 'moveFrom', 'moveTo', 'txbxContent')} | {WP + 'anchor'}
            if (len(blips) != 1 or inline_blips != blips
                    or any(n.tag in uncertain or n.tag.endswith('}AlternateContent')
                           for part in (picture, caption_paragraph) for n in part.iter())):
                return None, '芯片版图题注附近包含多图或兼容对象，未自动选择原图。'
            ref = blips[0].get(R + 'embed')
            if not ref or blips[0].get(R + 'link'):
                return None, '芯片版图不是唯一包内图片，未读取外部链接。'
            rels = ET.fromstring(archive.read('word/_rels/document.xml.rels'))
            relations = [r for r in rels if r.tag == PKG + 'Relationship' and r.get('Id') == ref]
            if len(relations) != 1:
                return None, '芯片版图图片关系缺失或不唯一，未自动带入原图。'
            relation = relations[0]
            target = relation.get('Target', '')
            if (relation.get('Type') != R[1:-1] + '/image' or relation.get('TargetMode', 'Internal') != 'Internal'
                    or not re.fullmatch(r'media/[^/\\:?#%]+\.(?:png|jpe?g)', target, re.I)
                    or target.split('/')[-1] in ('.', '..')):
                return None, '芯片版图仅支持唯一包内 PNG/JPEG 原图，未读取外部或不明确来源。'
            media_path = 'word/' + target
            info = archive.getinfo(media_path)
            if not 0 < info.file_size <= MAX_IMAGE_BYTES:
                return None, '设计原图超过 512 KiB 或为空，未自动带入；请人工导出较小 PNG/JPEG。'
            content = archive.read(info)
            mime = 'png' if PurePosixPath(target).suffix.lower() == '.png' else 'jpeg'
            # Reuse the existing bounded byte parser; no extra image decoder or
            # decompression-bomb exception may abort an otherwise readable DOCX.
            width, height = (_png_dimensions if mime == 'png' else _jpeg_dimensions)(content)
            source = (f'{filename}；{caption}；word/document.xml body[{index + 1}]；{media_path}；'
                      f'导入记录 {document_id}；文档 SHA-256 {document_sha256}；'
                      f'图片 SHA-256 {hashlib.sha256(content).hexdigest()}；设计原图，物理身份及耦合关系未确认')
            image = {'name': PurePosixPath(target).name, 'width': width, 'height': height, 'source': source,
                     'dataUrl': f'data:image/{mime};base64,' + base64.b64encode(content).decode('ascii')}
            validate_diagram_image(image)
            return image, None
    except APIError as error:
        return None, '设计原图未通过既有图片校验：' + error.message
    except (KeyError, ValueError, OSError, struct.error, zlib.error, ET.ParseError, zipfile.BadZipFile):
        return None, '设计原图或包内关系无法可靠解析，未自动带入，请人工选择。'
