import base64
import struct
import unittest
import zlib
import tempfile
from pathlib import Path
from backend.common import APIError
from backend.science_configuration import validate_science_configuration
from tests.test_controller import make_fixture


def png():
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(b'\0\xff\0\0')) + chunk(b'IEND', b'')


class DiagramAssetContracts(unittest.TestCase):
    def config(self, data=None):
        image={'name':'design.png','dataUrl':'data:image/png;base64,'+base64.b64encode(data or png()).decode(),'width':1,'height':1,'source':'design rev C'}
        return {'qubits':[], 'topology':{'nodes':[], 'edges':[], 'image':image}}

    def test_valid_bounded_image_is_accepted(self):
        validate_science_configuration('chip',self.config())

    def test_spoofed_image_and_svg_are_rejected_before_configuration_save(self):
        for data in [b'<svg onload="alert(1)"/>',png()[:-8],b'not a png']:
            with self.subTest(data=data[:15]), self.assertRaises(APIError):
                validate_science_configuration('chip',self.config(data))
        config=self.config();config['topology']['image']['dataUrl']='data:image/svg+xml;base64,PHN2Zy8+'
        with self.assertRaises(APIError):validate_science_configuration('chip',config)

    def test_wrong_dimensions_and_oversized_image_are_rejected(self):
        config=self.config();config['topology']['image']['width']=20
        with self.assertRaises(APIError):validate_science_configuration('chip',config)
        with self.assertRaises(APIError):validate_science_configuration('chip',self.config(b'x'*(513*1024)))

    def test_new_component_ports_validate_as_text(self):
        config={'signalPaths':[{'id':'r','label':'Read out','source':'p2','direction':'input','confirmation':'pending','components':[{'id':'c','type':'filter','label':'LPF','fromPort':{'bad':'value'}}]}]}
        with self.assertRaises(APIError):validate_science_configuration('deployment',config)

    def test_source_image_and_port_metadata_survive_existing_configuration_storage(self):
        with tempfile.TemporaryDirectory() as directory:
            controller,_=make_fixture(Path(directory))
            try:
                chip=controller.workspace.chips.save_chip({**self.config(),'operationId':'image-save','name':'design C'})
                loaded=controller.workspace.chips.get_chip(chip['id'])
                self.assertEqual(loaded['topology']['image'],self.config()['topology']['image'])
                self.assertEqual(loaded['versions'],[],'saving a reference must not publish it')
                path={'id':'read','label':'Read out','source':'OneNote p2','direction':'input','confirmation':'pending','sourceDate':'2026-11-25','station':'fixture','components':[{'id':'filter','type':'filter','label':'LPF','fromPort':'IN','toPort':'OUT','temperatureK':4,'attenuationDb':20,'confirmation':'pending'}]}
                draft=controller.workspace.chips.save_deployment({'operationId':'port-save','name':'fixture','signalPaths':[path]})
                self.assertEqual(controller.workspace.chips.get_deployment(draft['id'])['signalPaths'],[path])
            finally:controller.close()

    def test_jpeg_with_real_frame_and_scan_is_accepted_and_mime_spoof_is_rejected(self):
        encoded='/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDABALDA4MChAODQ4SERATGCgaGBYWGDEjJR0oOjM9PDkzODdASFxOQERXRTc4UG1RV19iZ2hnPk1xeXBkeFxlZ2P/2wBDARESEhgVGC8aGi9jQjhCY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2P/wAARCAADAAIDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDFoooryz7w/9k='
        config=self.config();config['topology']['image'].update(name='reference.jpg',dataUrl='data:image/jpeg;base64,'+encoded,width=2,height=3)
        validate_science_configuration('chip',config)
        config['topology']['image']['dataUrl']='data:image/png;base64,'+encoded
        with self.assertRaises(APIError):validate_science_configuration('chip',config)


if __name__=='__main__':unittest.main()
