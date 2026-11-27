"""Streaming index integrity, finite resources and non-ASCII byte offsets."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from backend.common import APIError
from backend import science_pulse_index as reader


class PulseIndex(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'compiled.json'

    def save(self,value):
        raw=json.dumps(value,ensure_ascii=False).encode('utf8');self.path.write_bytes(raw)
        return hashlib.sha256(raw).hexdigest()

    def test_unicode_and_chunk_boundary_preserve_exact_row_bytes(self):
        value=[{'rowId':0,'label':'中文'+('x'*66000)+'"\\]'}, {'rowId':7,'label':'独立复测'}]
        sha=self.save(value);index=reader.index_file(self.path,sha,'compiled')
        self.assertEqual(reader.read_row(self.path,index,7),value[1])
        self.assertEqual(reader.read_row(self.path,index,0),value[0])

    def test_cached_index_still_reads_hash_and_rejects_same_length_change(self):
        sha=self.save([{'rowId':0,'label':'before'}]);reader.index_file(self.path,sha,'compiled')
        with patch.object(reader._DECODER,'raw_decode',side_effect=AssertionError('cached lookup must not parse whole manifest')):
            index=reader.index_file(self.path,sha,'compiled')
        self.path.write_bytes(self.path.read_bytes().replace(b'before',b'after!'))
        with self.assertRaises(APIError):reader.index_file(self.path,sha,'compiled')
        with self.assertRaises(APIError):reader.read_row(self.path,index,0)

    def test_bounds_duplicates_trailing_bytes_and_nonfinite_are_rejected(self):
        for raw in (b'[{"rowId":0},{"rowId":0}]',b'[{"rowId":0},]',b'[{"rowId":0}]false',
                    b'[{"rowId":0,"rowId":1}]',b'[{"rowId":0,"x":NaN}]'):
            self.path.write_bytes(raw)
            with self.subTest(raw=raw),self.assertRaises((APIError,ValueError)):
                reader.index_file(self.path,hashlib.sha256(raw).hexdigest(),'compiled')
        sha=self.save([{'rowId':0}])
        with patch.object(reader,'MAX_MANIFEST_BYTES',2),self.assertRaises(APIError):reader.index_file(self.path,sha,'compiled')

    def test_axes_cache_retains_only_bounded_summary_and_cache_count_is_bounded(self):
        sha=self.save([{'id':0,'condition':'candidate','gates':['large unused structure']}])
        index=reader.index_file(self.path,sha,'axes')
        self.assertNotIn('gates',index['rows'][0])
        sha=self.save([{'id':0,'condition':'x'*257}])
        with self.assertRaises(APIError):reader.index_file(self.path,sha,'axes')
        for n in range(12):
            sha=self.save([{'rowId':n}]);reader.index_file(self.path,sha,'compiled')
        self.assertLessEqual(len(reader._CACHE),8)

    def test_unselected_row_corruption_truncation_and_new_sha_cannot_reuse_old_index(self):
        sha=self.save([{'rowId':0,'label':'keep'}, {'rowId':7,'label':'before'}])
        index=reader.index_file(self.path,sha,'compiled')
        self.assertEqual(reader.read_row(self.path,index,0)['label'],'keep')
        self.path.write_bytes(self.path.read_bytes().replace(b'before',b'after!'))
        with self.assertRaises(APIError):reader.index_file(self.path,sha,'compiled')
        updated=self.save([{'rowId':3,'label':'new identity'}])
        current=reader.index_file(self.path,updated,'compiled')
        self.assertEqual(set(current['rows']),{3})
        with self.assertRaises(APIError):reader.read_row(self.path,current,0)
        for raw in (b'[{"rowId":0',b'[{"rowId":0}',b'[{"rowId":0},'):
            self.path.write_bytes(raw)
            with self.subTest(raw=raw),self.assertRaises(APIError):
                reader.index_file(self.path,hashlib.sha256(raw).hexdigest(),'compiled')
