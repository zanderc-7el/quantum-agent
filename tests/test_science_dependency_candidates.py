"""Real controller aggregation keeps selection commit and verification fit separate."""
from copy import deepcopy
import json
import unittest
from backend.common import APIError,dumps
from tests import test_science_dependencies as fixtures


class CandidateReceiptContracts(unittest.TestCase):
    setUp=fixtures.ScienceDependencyContracts.setUp
    resolve=fixtures.ScienceDependencyContracts.resolve
    acquired=fixtures.ScienceDependencyContracts.acquired

    def source(self):
        original=self.adapter.execute
        def execute(request,emit,cancel):
            result=original(request,emit,cancel)
            if request['procedureId']=='sq_rabi_amplitude':
                result['candidateParameters']={'drive_amp':.41 if request['operationId'].endswith('/verify') else .4}
            return result
        self.adapter.execute=execute
        record,_=self.acquired()
        return record,self.wf

    def test_distinct_verification_candidate_resolves_without_changing_committed_selection(self):
        record,wf=self.source();proof=self.resolve(refs=[{'role':'rabi_amplitude','recordId':record['id']}],kind='measurement')
        self.assertEqual(proof['nodes'][0]['resolvedOutputs']['candidateParameters'],{'drive_amp':.4})
        self.assertEqual(next(n for n in wf['nodes'] if n['procedureId']=='sq_rabi_amplitude')['result']['verificationCandidateParameters'],{'drive_amp':.41})

    def test_missing_or_tampered_preserved_verification_value_is_rejected(self):
        record,wf=self.source()
        with self.c.store.connect() as rt:
            row=rt.db.execute('SELECT payload FROM platform_attempts WHERE id=?',(record['reference']['attemptId'],)).fetchone();original=json.loads(row[0])
        for preserved in (None,{'drive_amp':.42}):
            changed=deepcopy(original)
            if preserved is None:changed['result'].pop('verificationCandidateParameters')
            else:changed['result']['verificationCandidateParameters']=preserved
            with self.c.store.transaction() as rt:rt.db.execute('UPDATE platform_attempts SET payload=? WHERE id=?',(dumps(changed),changed['id']))
            with self.subTest(preserved=preserved),self.assertRaises(APIError) as error:
                self.resolve(refs=[{'role':'rabi_amplitude','recordId':record['id']}],kind='measurement')
            self.assertEqual(error.exception.code,'SCIENCE_DEPENDENCY_EVIDENCE')

    def test_committed_candidate_cannot_be_replaced_while_verify_receipt_stays_valid(self):
        record,_=self.source()
        with self.c.store.transaction() as rt:
            row=rt.db.execute('SELECT payload FROM platform_attempts WHERE id=?',(record['reference']['attemptId'],)).fetchone()
            changed=json.loads(row[0]);changed['result']['candidateParameters']={'drive_amp':.9}
            rt.db.execute('UPDATE platform_attempts SET payload=? WHERE id=?',(dumps(changed),changed['id']))
        with self.assertRaises(APIError) as error:self.resolve(refs=[{'role':'rabi_amplitude','recordId':record['id']}],kind='measurement')
        self.assertEqual(error.exception.code,'SCIENCE_DEPENDENCY_EVIDENCE')
