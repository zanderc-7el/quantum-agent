"""Offline contracts only; no instrument imports/connections."""
from copy import deepcopy
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest


def route():
    return dict(schema='qcal-bias-route/1',resourceId='Qfixture',deviceKey='fixture-device',
        lineId='fixture-line',board=2,channel=1,unit='V',polarity=1,measurementPlane='fixture-terminal',
        stationIdentity='FIXTURE-STATION',measurementInstrumentIdentity='FIXTURE-METER',
        minimumV=-.5,maximumV=.5,minimumMeasuredV=-.6,maximumMeasuredV=.6,maxStepV=.1,maxSlewVPerSecond=1.,maxRampSeconds=5.,
        settleSeconds=.02,stabilitySamples=3,readbackIntervalSeconds=.01,readTimeoutSeconds=.1,
        toleranceV=.005,restoreTimeoutSeconds=5.,maximumUncertaintyV=.001,
        preparedStaticDcEvidenceSha256='a'*64,confirmationEvidenceSha256='b'*64,
        providerId='fixture-measured',providerProtocolSha256='c'*64,restorePolicy='restore_initial_verified',
        offsetToOutput={'gain':1.,'offsetV':.02,'uncertaintyV':.0001,'evidenceSha256':'d'*64})


class Clock:
    def __init__(self):self.now=100.
    def monotonic(self):return self.now
    def sleep(self,delay):self.now+=delay


class Provider:
    provider_id='fixture-measured';protocol_sha256='c'*64
    measurement_kind='measured_output';setting_kind='hardware_setting_register'
    fixture_only=True;lock_names=('fixture-meter',)
    def __init__(self):self.setting=.1;self.calls=0;self.bad=False;self.closed=False
    def snapshot(self,*,route,timeout_seconds,nonce):
        self.calls+=1
        return {'providerId':self.provider_id,'protocolSha256':self.protocol_sha256,'nonce':nonce,
            'observedAt':dt.datetime.now(dt.timezone.utc).isoformat(),
            'outputState':{'kind':'hardware_setting_register','mode':'static_dc','outputEnabled':True,
                'sharedSettingsSha256':'e'*64,'raw':'FIXTURE MODE/OUTPUT/SHARED REGISTER'},
            'setting':{'kind':self.setting_kind,'valueV':self.setting,'raw':'REGISTER '+str(self.setting)},
            'measurement':{'kind':self.measurement_kind,'valueV':self.setting+.02+(1 if self.bad else 0),
                'uncertaintyV':.0001,'raw':'METER '+str(self.calls),'plane':route['measurementPlane'],
                'instrumentIdentity':route['measurementInstrumentIdentity']}}
    def close(self,*,timeout_seconds):self.closed=True


class FluxContracts(unittest.TestCase):
    def test_missing_provider_and_strict_route(self):
        from adapters.science_flux.contracts import validate_route,provider_for,ContractError
        self.assertEqual(validate_route(route())['board'],2)
        with self.assertRaisesRegex(ContractError,'provider'):provider_for(route())
        for key,value in [('unit','A'),('board',-1),('polarity',0),('maxStepV',float('nan')),('restorePolicy','zero')]:
            with self.subTest(key=key),self.assertRaises(ContractError):validate_route({**route(),key:value})
        incomplete=route();del incomplete['maximumV']
        with self.assertRaisesRegex(ContractError,'maximumV'):validate_route(incomplete)
        with self.assertRaisesRegex(ContractError,'restor'):validate_route({**route(),'maximumUncertaintyV':.003})

    def session(self,folder,provider=None,cancel=None,setter=None):
        from adapters.science_flux.bias_session import BiasSession
        clock=Clock();p=provider or Provider();writes=[]
        def write(value,timeout_seconds):
            state=json.loads((Path(folder)/'bias_journal.json').read_text())
            self.assertIn(state['state'],('write_pending','restoring'))
            self.assertEqual(state['initial']['setting']['valueV'],.1)
            writes.append(value);p.setting=value
            return {'commandReply':'ACK','measurement':False}
        session=BiasSession(route(),p,setter or write,Path(folder)/'bias_journal.json',
            identity={'operationId':'fixture-op','resourceId':'Qfixture'},check_cancel=cancel or (lambda:None),
            monotonic=clock.monotonic,sleep=clock.sleep)
        return session,p,writes,clock

    def test_ramp_actual_readback_journal_and_restore_setting_not_measured(self):
        with tempfile.TemporaryDirectory() as folder:
            s,p,w,c=self.session(folder);s.open_and_read_initial();point=s.ramp_to(-.2,'point-1')
            self.assertAlmostEqual(point['measurement']['valueV'],-.18)
            self.assertTrue(all(abs(b-a)<=.10000001 for a,b in zip([.1]+w,w)))
            self.assertGreaterEqual(c.now-100,.3)
            final=s.restore_initial('completed');self.assertTrue(final['verified']);self.assertAlmostEqual(w[-1],.1)
            state=json.loads((Path(folder)/'bias_journal.json').read_text());self.assertEqual(state['state'],'restored')
            self.assertNotEqual(state['initial']['setting']['valueV'],state['initial']['measurement']['valueV'])

    def test_cache_or_bad_measurement_has_no_write(self):
        for cache in (True,False):
            with self.subTest(cache=cache),tempfile.TemporaryDirectory() as folder:
                p=Provider();p.setting_kind='software_cache' if cache else 'hardware_setting_register';p.bad=not cache
                s,p,w,c=self.session(folder,p)
                with self.assertRaises(ValueError):s.open_and_read_initial()
                self.assertEqual(w,[])

    def test_cancel_during_ramp_restores_ignoring_user_stop(self):
        with tempfile.TemporaryDirectory() as folder:
            def cancel():
                if w:raise InterruptedError('STOP')
            s,p,w,c=self.session(folder,cancel=cancel);s.open_and_read_initial()
            with self.assertRaises(InterruptedError):s.ramp_to(-.4,'point')
            self.assertTrue(s.restore_initial('cancelled')['verified']);self.assertAlmostEqual(p.setting,.1)

    def test_unknown_write_fresh_read_restoration_and_restart_no_replay(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Provider();writes=[]
            def timeout(value,timeout_seconds):p.setting=value;writes.append(value);raise TimeoutError('reply lost')
            s,p,w,c=self.session(folder,p,setter=timeout);s.open_and_read_initial()
            with self.assertRaises(TimeoutError):s.ramp_to(.3,'point')
            before=list(writes)
            s2,_,_,_=self.session(folder,p,setter=timeout)
            with self.assertRaisesRegex(ValueError,'journal'):s2.open_and_read_initial()
            self.assertEqual(writes,before)
            restored=s.restore_initial('write_timeout');self.assertFalse(restored['verified'])
            self.assertEqual(json.loads((Path(folder)/'bias_journal.json').read_text())['state'],'needs_reconciliation')

    def test_readback_failure_does_not_allow_acquisition(self):
        with tempfile.TemporaryDirectory() as folder:
            s,p,w,c=self.session(folder);s.open_and_read_initial();p.bad=True
            with self.assertRaises(ValueError):s.ramp_to(.2,'point')
            self.assertFalse(s.restore_initial('failure')['verified'])


if __name__=='__main__':unittest.main()
