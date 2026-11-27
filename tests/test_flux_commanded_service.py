"""Synthetic registered DB operations test save/query/restore; not hardware evidence."""
from copy import deepcopy
import hashlib,json,tempfile,unittest
from pathlib import Path
from backend.common import APIError,digest,dumps,now
from backend.science_service import ScienceService
from backend.flux_points import resolve_point
from backend.quality import evaluate,SCIENCE_FLUX_COMMAND_GATES as SCIENCE_FLUX_GATES,SCIENCE_FLUX_COMMAND_GATES_VERSION as SCIENCE_FLUX_GATES_VERSION
from tests import test_flux_point_service as old
class CommandedPointService(unittest.TestCase):
    setUp=old.FluxPointServiceContracts.setUp
    save=old.FluxPointServiceContracts.save
    def native_source_fixture(self):
        fixture=json.loads((Path(__file__).parent/'fixtures/science_flux_commanded_numeric_result.json').read_text(encoding='utf8'))
        self.assertTrue(fixture['neverAcquiredFromHardware'])
        wf=self.c.create({'name':'synthetic flux source','mode':'real','resourceIds':['Q11'],'coverage':'main'})
        self.c.action(wf['id'],{'action':'start','operationId':'start-fixture','expectedRevision':wf['revision']});self.c.run_workflow(wf['id'])
        with self.c.store.transaction() as rt:
            wf=self.c.store.get(rt,wf['id']);node=wf['nodes'][0];node['procedureId']='ro_flux_scan'
            attempt=json.loads(rt.db.execute('SELECT payload FROM platform_attempts WHERE id=?',(node['latestAttemptId'],)).fetchone()[0])
            attempt['procedureId']='ro_flux_scan';results=[]
            for index,operation in enumerate((attempt['operationId'],attempt['verificationOperationId'])):
                row=rt.db.execute('SELECT request FROM platform_effects WHERE operation_id=?',(operation,)).fetchone()
                request=json.loads(row[0]);result=deepcopy(fixture['selectionResult' if index==0 else 'verificationResult'])
                request.update(procedureId='ro_flux_scan',parameters=result['parameters']);request.pop('scienceDependencies',None)
                result.update(acquiredAt=now(),sourceRunId=operation)
                result['provenance'].pop('scienceDependencies',None)
                flux=result['provenance']['scienceFlux'];flux['route']['resourceId']='Q11';flux['routeSha256']=digest(flux['route'])
                context=flux['biasContext'];context.update(resourceId='Q11',operationId=operation,routeSha256=flux['routeSha256'])
                context['sha256']=digest({k:v for k,v in context.items() if k!='sha256'})
                from adapters.science_flux.contracts import command_authorization
                from adapters.science_flux.command_lease import lease_context
                entry=None
                if index:
                    previous=results[0]['provenance']['scienceFlux'];end=previous['restoration']
                    entry={'kind':'prior_end_command','operationId':attempt['operationId'],'completionSha256':previous['completionSha256'],'commandSha256':end['command']['sha256'],'receiptSha256':digest(end),'leaseTokenSha256':digest(request['leaseToken']),'lockTokenSha256':previous['commandContinuation']['lockTokenSha256']}
                from tests.test_science_flux_commanded import confirmation
                auth=command_authorization(flux['route'],operation,stage='verification' if index else 'selection',entry_evidence=entry,
                    **({} if index else {'start_confirmation':confirmation(request,flux['route'])}))
                request['biasCommandAuthorization']=auth;flux['commandAuthorization']=auth;flux['commandLease']=lease_context(request,auth['stage'])
                for receipt in [flux['initial'],flux['restoration']]+[p['biasReceipt'] for p in flux['points']]:
                    command=receipt['command'];command.update(operationId=operation,resourceId='Q11',routeSha256=flux['routeSha256'])
                    command['sha256']=digest({k:v for k,v in command.items() if k!='sha256'})
                    receipt['authorization']=auth;receipt['authorizationSha256']=digest(auth)
                flux['completionSha256']=digest({'fixtureOnly':operation})
                if not index:
                    continuation=flux['commandContinuation'];continuation.update(operationId=operation,lease=flux['commandLease'],routeSha256=flux['routeSha256'],endCommandSha256=flux['restoration']['command']['sha256'],endReceiptSha256=digest(flux['restoration']))
                    continuation['sha256']=digest({k:v for k,v in continuation.items() if k!='sha256'})
                artifacts=[]
                for n,point in enumerate(flux['points']):
                    path=Path(self.tmp.name)/f'raw-{index}-{n}.npz';path.write_bytes(f'fixture-only ADC {operation} {n}'.encode())
                    sha=hashlib.sha256(path.read_bytes()).hexdigest();point.update(rawSha256=sha,rawPath=str(path))
                    point['cutSha256']=digest({k:v for k,v in point.items() if k!='cutSha256'})
                    artifacts.append(self.c._artifact(rt,{'id':digest(str(path)),'name':path.name,'kind':'raw','sha256':sha,'localPath':str(path)}))
                result['artifacts']=artifacts;result=evaluate(result,'ro_flux_scan','real',SCIENCE_FLUX_GATES,independent=bool(index),rule_version=SCIENCE_FLUX_GATES_VERSION)
                results.append(result);rt.db.execute('UPDATE platform_effects SET request=?,result=? WHERE operation_id=?',(dumps(request),dumps(result),operation))
            attempt.update(result={**results[1],'selectionResult':results[0],'verificationCandidateParameters':{}},state='verified')
            node.update(result=deepcopy(attempt['result']),engineState='verified',parameters=deepcopy(results[1]['parameters']))
            wf.update(gates=deepcopy(SCIENCE_FLUX_GATES),gatesVersion=SCIENCE_FLUX_GATES_VERSION,gatesSha256=digest(SCIENCE_FLUX_GATES))
            rt.db.execute('UPDATE platform_attempts SET payload=? WHERE id=?',(dumps(attempt),attempt['id']))
            self.c.store.save(rt,wf,'synthetic_flux_source_fixture')
        self.c.profile['biasRoutes']={'Q11':deepcopy(flux['route'])}
        return self.save('measurement',{},reference={'workflowId':wf['id'],'attemptId':attempt['id']})
    def test_native_point_save_reresolve_and_source_corruption_blocks_without_dispatch(self):
        source=self.native_source_fixture();raw=source['result']['scienceFlux']['points'][-1]
        inputs={'sourceRecordId':source['id'],'selectedBiasIndex':0,'purpose':'idle','bias':raw['requestedSettingV'],'biasUnit':'V'}
        point=self.save('working_point',inputs)
        self.assertEqual(point['result']['source'],'commanded_point_derived');self.assertFalse(point['acceptedCalibration']);self.assertIsNone(point['result']['point']['measuredOutputV']);self.assertEqual(source['result']['grid']['biasMeaning'],'commanded_setpoint')
        self.assertEqual(point['result']['point']['fluxChannel'],source['result']['scienceFlux']['route']['lineId'])
        with self.c.store.connect() as rt:
            evidence=resolve_point(self.c,rt,point['id'],resource_id='Q11')
        self.assertEqual(evidence['workingPointId'],point['id']);self.assertEqual(evidence['recordId'],source['id'])
        self.assertEqual(evidence['originalCut']['readoutFrequencyHz'],raw['readoutFrequencyHz'])
        Path(raw['rawPath']).write_bytes(b'changed')
        with self.c.store.connect() as rt,self.assertRaises(APIError):resolve_point(self.c,rt,point['id'],resource_id='Q11')
