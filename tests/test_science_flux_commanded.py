"""Commanded bias is real setter execution, never a fabricated voltage readback."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from tests.test_science_flux import Clock

def confirmation(identity,route_value=None):
    from adapters.science_runtime.policy import digest
    r=route_value or route()
    identity={'operationId':identity} if isinstance(identity,str) else identity
    result={'schema':'qcal-bias-start-confirmation/1','runId':'TEST_ONLY-run','nodeId':'TEST_ONLY-node',
        'resourceId':r['resourceId'],'attemptId':'TEST_ONLY-attempt',
        **{k:v for k,v in identity.items() if k in ('operationId','runId','nodeId','resourceId','attemptId')},
        'confirmedAt':'2026-11-26T00:00:00+00:00','routeSha256':digest(r),'startSettingV':r['startSettingV'],'endSettingV':r['endSettingV'],
        'commandOperationId':'TEST_ONLY-manual-command','confirmedRevision':1}
    result['confirmationId']=digest(result);return result


def route():
    return {'schema':'qcal-bias-command-route/1','resourceId':'Qfixture','deviceKey':'fixture-device',
        'lineId':'fixture-line','stationIdentity':'FIXTURE-STATION','board':2,'channel':1,'unit':'V','polarity':1,
        'minimumV':-.5,'maximumV':.5,'maxStepV':.1,'maxSlewVPerSecond':1.,'maxRampSeconds':5.,
        'settleSeconds':.02,'commandTimeoutSeconds':.1,'endTimeoutSeconds':5.,
        'startSettingV':.1,'endSettingV':-.1,'endPolicy':'return_declared_setting',
        'preparedStaticDcEvidenceSha256':'a'*64,'confirmationEvidenceSha256':'b'*64,
        'startSettingEvidenceSha256':'c'*64,'endSettingEvidenceSha256':'d'*64,
        'commandReplyContract':{'kind':'exact_tokens','moduleReplies':['fixture module'],'channelReplies':['fixture channel'],
            'offsetReplies':['fixture offset'],'evidenceSha256':'e'*64}}


class CommandedContracts(unittest.TestCase):
    def test_start_confirmation_is_per_operation_and_cannot_come_from_preview(self):
        from adapters.science_flux.contracts import command_authorization
        from adapters.science_flux.command_contracts import validate_authorization
        r=route();identity={'operationId':'measure','runId':'run','nodeId':'node','resourceId':'Qfixture','attemptId':'attempt'}
        with self.assertRaises(ValueError):command_authorization(r,'measure')
        c=confirmation(identity,r);auth=command_authorization(r,'measure',start_confirmation=c)
        self.assertEqual(validate_authorization(auth,r,identity),auth)
        for key,value in [('operationId','other'),('runId','other'),('nodeId','other'),('attemptId','other'),
                          ('routeSha256','0'*64),('confirmedRevision',True),('confirmedAt','2026-11-26'),('startSettingV',False)]:
            altered=deepcopy(auth);altered['entryEvidence']['confirmation'][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):validate_authorization(altered,r,identity)
        with self.assertRaises(ValueError):command_authorization(r,'budget',stage='plan_only',start_confirmation=c)

    def test_new_route_has_no_provider_or_meter_and_old_route_keeps_strict_contract(self):
        from adapters.science_flux.contracts import validate_route,policy_for,provider_for
        from tests.test_science_flux import route as measured
        self.assertEqual(policy_for(validate_route(route())),'f8_offset_only_commanded_return_v2')
        self.assertIsNone(provider_for(route()))
        with self.assertRaises(ValueError):provider_for(measured())
        for key,value in [('board',-1),('unit','A'),('startSettingV',.6),('endSettingV',None),('startSettingEvidenceSha256',''),('endPolicy','restore_initial_verified'),('maxStepV',1e-12)]:
            with self.subTest(key=key),self.assertRaises(ValueError):validate_route({**route(),key:value})
        with self.assertRaises(ValueError):validate_route({**route(),'providerId':'invented'})

    def session(self,folder,*,fail=False,cancel=None):
        from adapters.science_flux.command_session import CommandedBiasSession
        from adapters.science_flux.command_contracts import authorization
        r=route();clock=Clock();writes=[];path=Path(folder)/'bias_journal.json'
        identity={'operationId':'fixture-operation','resourceId':'Qfixture'}
        def setter(value,*,timeout_seconds):
            state=json.loads(path.read_text());self.assertEqual(state['events'][-1]['outcome'],'unknown')
            self.assertEqual(state['events'][-1]['valueV'],value);writes.append(value)
            if fail and len(writes)==2:raise TimeoutError('write outcome unknown')
            return {'nativeFunctions':['boardSelect','channSelect','setOffset'],'replies':['fixture module','fixture channel','fixture offset'],
                    'requestedSettingV':value,'unit':'V','measuredOutput':False}
        s=CommandedBiasSession(r,None,setter,path,identity=identity,authorization=authorization(r,identity,start_confirmation=confirmation(identity,r)),
            check_cancel=cancel or (lambda:None),monotonic=clock.monotonic,sleep=clock.sleep)
        return s,writes,clock

    def test_actual_start_ramp_and_declared_end_have_no_measured_value(self):
        from adapters.science_flux.command_contracts import validate_command_receipt
        with tempfile.TemporaryDirectory() as folder:
            s,w,c=self.session(folder);initial=s.open_and_read_initial();held=s.ramp_to(.3,'bias-0');after=s.verify_held('bias-0')
            self.assertEqual(w[0],.1);self.assertEqual(w[-1],.3);self.assertEqual(held['command'],after['command'])
            self.assertIsNone(held['measurement']);self.assertFalse(held['physicalBiasVerified'])
            self.assertEqual(held['setting']['kind'],'commanded_setpoint')
            validate_command_receipt(held,route(),setting=.3)
            end=s.restore_initial('completed');self.assertTrue(end['commandEndVerified']);self.assertFalse(end['verified'])
            self.assertEqual(w[-1],-.1);self.assertNotEqual(w[-1],initial['setting']['valueV'])
            self.assertTrue(all(abs(b-a)<=.1000000001 for a,b in zip(w,w[1:])))
            state=json.loads((Path(folder)/'bias_journal.json').read_text());self.assertEqual(state['state'],'returned_to_declared_setting')
            self.assertGreater(c.now-100,.6)

    def test_stop_uses_independent_end_deadline_but_unknown_write_never_guesses_ramp(self):
        with tempfile.TemporaryDirectory() as folder:
            stopped=[False]
            def cancel():
                if stopped[0]:raise InterruptedError('STOP')
            s,w,_=self.session(folder,cancel=cancel);s.open_and_read_initial();s.ramp_to(.3,'bias-0');stopped[0]=True
            with self.assertRaises(InterruptedError):s.verify_held('bias-0')
            self.assertTrue(s.restore_initial('cancelled')['commandEndVerified']);self.assertEqual(w[-1],-.1)
        with tempfile.TemporaryDirectory() as folder:
            s,w,_=self.session(folder,fail=True);s.open_and_read_initial()
            with self.assertRaises(TimeoutError):s.ramp_to(.3,'bias-0')
            count=len(w);end=s.restore_initial('write_timeout');self.assertFalse(end['commandEndVerified']);self.assertEqual(len(w),count)
            self.assertEqual(json.loads((Path(folder)/'bias_journal.json').read_text())['state'],'needs_reconciliation')
            s2,_,_=self.session(folder)
            with self.assertRaisesRegex(ValueError,'journal'):s2.open_and_read_initial()

    def test_authorization_and_receipt_tampering_rejected(self):
        from adapters.science_flux.command_contracts import authorization,validate_authorization,validate_command_receipt
        identity={'operationId':'fixture','resourceId':'Qfixture'};a=authorization(route(),identity,start_confirmation=confirmation(identity))
        for key,value in [('operationId','other'),('startSettingV',0),('endSettingV',0),('routeSha256','f'*64)]:
            with self.subTest(key=key),self.assertRaises(ValueError):validate_authorization({**a,key:value},route(),identity)
        with tempfile.TemporaryDirectory() as folder:
            s,_,_=self.session(folder);held=s.open_and_read_initial()
            for mutate in (lambda r:r.update(measurement={'kind':'measured_output','valueV':.1}),lambda r:r['command'].update(valueV=.2),lambda r:r['settle'].update(elapsedSeconds=0)):
                bad=deepcopy(held);mutate(bad)
                with self.assertRaises(ValueError):validate_command_receipt(bad,route(),setting=.1)

    def test_campaign_rejects_total_command_budget_before_any_transmission(self):
        from adapters.science_flux.plans import build_campaign
        from adapters.science_flux.contracts import validate_route
        from tests.test_science_flux_plans import parameters
        r=route();r.update(maxStepV=1/2048,maxRampSeconds=30.,endTimeoutSeconds=30.,commandTimeoutSeconds=.01)
        validate_route(r)  # Each full-range ramp is individually legal.
        p=parameters();p['readout_frequencies_hz']=p['readout_frequencies_hz'][:5]
        p['bias_values_v']=[(-.5+i*.01) if i%2==0 else (.5-i*.01) for i in range(21)]
        with self.assertRaisesRegex(ValueError,'4096'):build_campaign('ro_flux_scan',p,r)

    def test_stop_after_first_known_start_command_still_has_verifiable_end(self):
        from adapters.science_flux.command_session import CommandedBiasSession
        from adapters.science_flux.contracts import command_authorization
        from adapters.science_flux.command_contracts import validate_command_journal
        stopped=[False];r=route();clock=Clock();identity={'operationId':'stop-start','resourceId':'Qfixture'}
        def check():
            if stopped[0]:raise InterruptedError('STOP after known start command')
        def setter(v,**kw):
            stopped[0]=True
            return {'nativeFunctions':['boardSelect','channSelect','setOffset'],'replies':['fixture module','fixture channel','fixture offset'],
                'requestedSettingV':v,'unit':'V','measuredOutput':False}
        with tempfile.TemporaryDirectory() as t:
            path=Path(t)/'journal.json';auth=command_authorization(r,identity['operationId'],start_confirmation=confirmation(identity,r))
            s=CommandedBiasSession(r,None,setter,path,identity=identity,authorization=auth,check_cancel=check,
                monotonic=clock.monotonic,sleep=clock.sleep)
            with self.assertRaises(InterruptedError):s.open_and_read_initial()
            end=s.restore_initial('cancelled');self.assertTrue(end['commandEndVerified'])
            validate_command_journal(json.loads(path.read_text()),r,identity,auth,allow_partial_initial=True)


if __name__=='__main__':unittest.main()
