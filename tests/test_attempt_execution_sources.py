"""Normal lifecycle and original source consumers in an isolated test library."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from adapters.arclight import ArcLightAdapter
from adapters.arclight_rabi16_adapter import ArcLightRabi16Adapter
from backend.common import APIError, ROOT, now
from backend.controller import Controller
from tests import test_gate_calibration_context as legacy
from tests.rabi16_archive_fixture import archive_result


class AttemptVersionSources(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='r31-source-test-only-');self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);folder=self.root/'data/imports';folder.mkdir(parents=True)
        (folder/'history.json').write_text(json.dumps({'version':1,'resources':[{'id':'Q11','label':'Q11'}],'records':[]}),encoding='utf-8')
        profile=json.loads((ROOT/'adapters/arclight_profile_v3.json').read_text(encoding='utf-8'))
        profile.update(enabled=True,connectionConfirmedAt=now())
        self.adapter=ArcLightAdapter(ROOT,profile);self.adapter.operations=self.root/'data/runs/arclight_operations'
        self.calls=[];self.counter=0
        self.adapter.execute=lambda request,*args:legacy.GateContextContracts.transport(self,request,*args)
        self.adapter.with_execution_enabled=lambda:self.adapter
        self.c=Controller(self.root,adapter=self.adapter,start_worker=False);self.addCleanup(self.c.close)
        self.c.hardware_ownership=lambda **kwargs:{'verified':True,'owners':[]}
        self.c._brain=Mock(return_value={'status':'waiting_model','message':'TEST ONLY'})
        original=ArcLightRabi16Adapter.__init__
        def initialize(adapter,*args):
            original(adapter,*args);adapter.operations=self.adapter.operations
        self.init_patch=patch.object(ArcLightRabi16Adapter,'__init__',initialize);self.init_patch.start();self.addCleanup(self.init_patch.stop)
        def execute(adapter,request,*args):
            self.calls.append(request);return archive_result(adapter,request)
        self.execute_patch=patch.object(ArcLightRabi16Adapter,'execute',execute);self.execute_patch.start();self.addCleanup(self.execute_patch.stop)
        self.owner_patch=patch.object(ArcLightAdapter,'hardware_ownership',return_value={'verified':True,'owners':[]});self.owner_patch.start();self.addCleanup(self.owner_patch.stop)
        self.preflight_patch=patch.object(ArcLightAdapter,'preflight',return_value={'ready':True,'enabled':False});self.preflight_patch.start();self.addCleanup(self.preflight_patch.stop)

    def command(self,wf,action,**extra):
        return legacy.GateContextContracts.command(self,wf,action,**extra)

    def accepted(self,coverage='main'):
        wf=legacy.GateContextContracts.create(self,coverage=coverage,length=375e-9)
        with self.c.store.transaction() as rt:
            wf=self.c.get(wf['id'],rt);wf['hardwareConfirmed']=True
            self.c.store.save(rt,wf,'TEST_ONLY injected transport permission')
        wf=self.c.get(wf['id'])
        for procedure in ['ro_resonator_survey']+(['ro_power_shift'] if coverage=='full' else [])+['sq_spectroscopy']:
            node=next(n for n in wf['nodes'] if n['procedureId']==procedure)
            self.command(wf,'run_step',nodeId=node['id']);self.c.run_workflow(wf['id']);wf=self.c.get(wf['id'])
            self.assertEqual(self.c._node(wf,node['id'])['engineState'],'awaiting_acceptance')
            wf=self.command(wf,'approve',nodeId=node['id'])
        node=next(n for n in wf['nodes'] if n['procedureId']=='sq_rabi_amplitude')
        wf=self.c.set_parameters(wf['id'],node['id'],{'operationId':'r31-parameters','expectedRevision':wf['revision'],
            'parameters':{'shots':2000,'acquisitions_per_point':8,'points':41,'capture_repeats':2}})
        with self.c.store.transaction() as rt:
            wf=self.c.get(wf['id'],rt);wf['currentNodeId']=node['id']
            self.c.store.save(rt,wf,'TEST_ONLY current amplitude admission target')
        wf=self.c.get(wf['id'])
        wf=self.c.select_execution_version(wf['id'],node['id'],{'operationId':'r31-select','expectedRevision':wf['revision'],
            'executionVersion':'rabi16-v1','reason':'Explicit TEST ONLY next attempt'})
        self.command(wf,'run_step',nodeId=node['id']);self.c.run_workflow(wf['id']);wf=self.c.get(wf['id'])
        self.assertEqual(self.c._node(wf,node['id'])['engineState'],'awaiting_acceptance',self.c._node(wf,node['id']).get('detail'))
        return self.command(wf,'approve',nodeId=node['id'])

    def test_new_complete_sampling_source_then_ordinary_time_and_iq_keep_v3(self):
        wf=self.accepted('full')
        rabi=next(n for n in wf['nodes'] if n['procedureId']=='sq_rabi_amplitude')
        from backend.gate_calibration_context import _operation
        with self.c.store.connect() as rt:
            attempt=json.loads(rt.db.execute('SELECT payload FROM platform_attempts WHERE id=?',(rabi['latestAttemptId'],)).fetchone()[0])
            for stage,op in [('selection',attempt['operationId']),('verification',attempt['verificationOperationId'])]:
                result,raw=_operation(self.c,rt,wf,rabi,attempt,op,stage)
                self.assertGreater(len(result['artifacts']),1390);self.assertTrue(raw)
                self.assertEqual(result['provenance']['samplingReceipt']['totalShots'],2624000)
        from backend.science_service import ScienceService
        from backend.science_dependencies import ScienceDependencyResolver
        record=ScienceService(self.c).write('/records',{'operationId':'archive-test-only-amplitude',
            'kind':'measurement','input':{},'scope':{},'reference':{'workflowId':wf['id'],
            'attemptId':rabi['latestAttemptId']}},'POST')
        with self.c.store.connect() as rt:
            proof=ScienceDependencyResolver(self.c).resolve(rt,kind='measurement',scope={},
                refs=[{'role':'rabi_amplitude','recordId':record['id']}],inputs={})
        source=proof['nodes'][0]
        self.assertEqual(source['adapterId'],'arclight-private-f8-q11-rabi16-v1')
        self.assertEqual(source['executionBinding'],attempt['executionBinding'])
        self.assertEqual(source['resolvedOutputs']['candidateParameters']['drive_amp'],.8)
        for procedure in ('sq_rabi_time','ro_iq01'):
            node=next(n for n in wf['nodes'] if n['procedureId']==procedure)
            self.command(wf,'run_step',nodeId=node['id']);self.c.run_workflow(wf['id']);wf=self.c.get(wf['id'])
            self.assertEqual(self.c._node(wf,node['id'])['engineState'],'awaiting_acceptance',self.c._node(wf,node['id']).get('detail'))
            wf=self.command(wf,'approve',nodeId=node['id'])
        downstream=[r for r in self.calls if r['procedureId'] in ('sq_rabi_time','ro_iq01')]
        self.assertEqual(len(downstream),4)
        self.assertTrue(all(r['adapterId']=='arclight-private-f8-q11-v3' and 'executionBinding' not in r for r in downstream))

    def test_sampling_receipt_missing_changed_or_unregistered_never_forms_gate_source(self):
        wf=self.accepted();node=next(n for n in wf['nodes'] if n['procedureId']=='sq_rabi_amplitude')
        from backend.gate_calibration_context import _operation
        with self.c.store.connect() as rt:
            attempt=json.loads(rt.db.execute('SELECT payload FROM platform_attempts WHERE id=?',(node['latestAttemptId'],)).fetchone()[0])
            op=attempt['operationId'];directory=self.adapter._operation_dir(op)
            for relative in ('sampling_receipt.json','results/TEST_ONLY/point_0000_block_000.npz'):
                path=directory/relative;original=path.read_bytes();path.write_bytes(original+b'changed')
                try:
                    with self.assertRaises((APIError,ValueError)):_operation(self.c,rt,wf,node,attempt,op,'selection')
                finally:path.write_bytes(original)
            row=rt.db.execute("SELECT * FROM platform_artifacts WHERE path=?",(str(directory/'sampling_receipt.json'),)).fetchone()
            self.assertIsNotNone(row)
            rt.db.execute('DELETE FROM platform_artifacts WHERE id=?',(row['id'],))
            with self.assertRaises(APIError):_operation(self.c,rt,wf,node,attempt,op,'selection')


if __name__=='__main__':unittest.main()
