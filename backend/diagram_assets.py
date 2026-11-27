"""Bounded embedded PNG/JPEG reference images; no file writes or remote requests."""
import base64
import binascii
import re
import struct
import zlib
from .common import APIError

MAX_IMAGE_BYTES = 512 * 1024
MAX_IMAGE_URL_CHARS = 700 * 1024


def _require(ok, message='原图内容损坏或不是有效 PNG/JPEG'):
    if not ok:
        raise APIError('SCIENCE_CONFIGURATION_INVALID', message)


def _dimensions(width, height):
    _require(0 < width <= 8192 and 0 < height <= 8192 and width * height <= 16_000_000,
             '原图边长不得超过8192像素，总像素不得超过1600万')


def _png_dimensions(data):
    _require(data.startswith(b'\x89PNG\r\n\x1a\n'))
    offset = 8
    dimensions = None
    compressed = bytearray()
    ended = False
    while offset + 12 <= len(data):
        size = struct.unpack_from('>I', data, offset)[0]
        kind = data[offset + 4:offset + 8]
        _require(offset + 12 + size <= len(data))
        body = data[offset + 8:offset + 8 + size]
        checksum = struct.unpack_from('>I', data, offset + 8 + size)[0]
        _require(zlib.crc32(kind + body) == checksum)
        if dimensions is None:
            _require(kind == b'IHDR' and size == 13)
            width, height, depth, color, compression, filtering, interlace = struct.unpack('>IIBBBBB', body)
            _dimensions(width, height)
            _require(color in (0, 2, 3, 4, 6) and depth in {0:(1,2,4,8,16),2:(8,16),3:(1,2,4,8),4:(8,16),6:(8,16)}[color])
            _require(compression == 0 and filtering == 0 and interlace in (0,1))
            dimensions = width, height
        elif kind == b'IHDR':
            _require(False)
        if kind == b'IDAT':
            compressed.extend(body)
        offset += size + 12
        if kind == b'IEND':
            _require(size == 0 and offset == len(data) and compressed)
            ended = True
            break
    _require(ended and dimensions)
    channels = {0:1,2:3,3:1,4:2,6:4}[color]
    passes = [(0,0,1,1)] if not interlace else [(0,0,8,8),(4,0,8,8),(0,4,4,8),(2,0,4,4),(0,2,2,4),(1,0,2,2),(0,1,1,2)]
    rows = []
    for x,y,dx,dy in passes:
        w,h = max(0,(width-x+dx-1)//dx),max(0,(height-y+dy-1)//dy)
        if w and h:
            rows.append(((w*channels*depth+7)//8+1,h))
    expected = sum(length*count for length,count in rows)
    decoder = zlib.decompressobj()
    decoded = decoder.decompress(compressed, expected+1)
    _require(decoder.eof and not decoder.unused_data and not decoder.unconsumed_tail and len(decoded) == expected)
    offset = 0
    for length,count in rows:
        for _ in range(count):
            _require(decoded[offset] <= 4)
            offset += length
    return dimensions


def _jpeg_dimensions(data):
    _require(data.startswith(b'\xff\xd8') and data.endswith(b'\xff\xd9'))
    offset, dimensions, scan, quantization, huffman = 2, None, False, False, False
    while offset < len(data):
        _require(data[offset] == 0xff)
        while offset < len(data) and data[offset] == 0xff:
            offset += 1
        _require(offset < len(data))
        marker = data[offset]
        offset += 1
        if marker == 0xd9:
            _require(offset == len(data) and dimensions and scan and quantization and huffman)
            return dimensions
        _require(marker not in (0,0xd8) and not 0xd0 <= marker <= 0xd7 and offset+2 <= len(data))
        size = struct.unpack_from('>H',data,offset)[0]
        _require(size >= 2 and offset+size <= len(data))
        body = data[offset+2:offset+size]
        if marker in (0xc0,0xc1,0xc2):
            _require(len(body) >= 6 and body[0] in (8,12))
            height,width = struct.unpack_from('>HH',body,1)
            _dimensions(width,height)
            _require(body[5] in (1,3,4) and len(body) == 6+3*body[5])
            dimensions = width,height
        elif marker == 0xdb:
            _require(len(body) >= 65)
            quantization = True
        elif marker == 0xc4:
            _require(len(body) >= 17)
            huffman = True
        offset += size
        if marker == 0xda:
            _require(dimensions and len(body) >= 6 and len(body) == 4+2*body[0])
            scan = True
            # Entropy bytes escape 0xff as 0xff00; restart markers stay within a scan.
            while offset < len(data):
                if data[offset] != 0xff:
                    offset += 1
                elif offset+1 < len(data) and (data[offset+1] == 0 or 0xd0 <= data[offset+1] <= 0xd7):
                    offset += 2
                else:
                    break
    _require(False)


def validate_diagram_image(image):
    _require(isinstance(image,dict),'原图需提供文件名、图片内容与像素尺寸')
    _require(isinstance(image.get('name'),str) and 0 < len(image['name'].strip()) <= 500,'原图文件名不能为空或过长')
    url = image.get('dataUrl')
    _require(isinstance(url,str) and len(url) <= MAX_IMAGE_URL_CHARS,'原图编码不得超过700 KiB')
    match = re.fullmatch(r'data:image/(png|jpeg);base64,([A-Za-z0-9+/]+={0,2})',url)
    _require(match,'原图仅支持内嵌 PNG/JPEG，不支持SVG或远程地址')
    try:
        data = base64.b64decode(match[2],validate=True)
        _require(0 < len(data) <= MAX_IMAGE_BYTES,'原图须小于512 KiB，请先导出较小PNG/JPEG')
        dimensions = _png_dimensions(data) if match[1] == 'png' else _jpeg_dimensions(data)
    except (ValueError,struct.error,binascii.Error,zlib.error) as exc:
        raise APIError('SCIENCE_CONFIGURATION_INVALID','原图无法解析为PNG/JPEG') from exc
    _require(all(type(image.get(k)) is int for k in ('width','height')) and dimensions == (image['width'],image['height']),
             '原图记录的像素尺寸与实际图片不符')
    for key in ('source','date'):
        if key in image:
            _require(isinstance(image[key],str) and 0 < len(image[key].strip()) <= 2000,'原图来源或日期格式错误')
    return len(url)
