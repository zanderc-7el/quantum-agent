"""Bounded bias state machine with write-ahead, fsync'd restoration journal."""
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import time
import uuid
from .contracts import validate_route,validate_snapshot,number,target_output

def durable_save(path,value):
    path=Path(path);temp=path.with_suffix(path.suffix+'.partial')
    with temp.open('w',encoding='utf-8') as stream:
        json.dump(value,stream,ensure_ascii=False,allow_nan=False,indent=2);stream.flush();os.fsync(stream.fileno())
    os.replace(temp,path)

class BiasSession:
    def __init__(self,route,provider,setter,journal,*,identity,check_cancel,monotonic=time.monotonic,sleep=time.sleep):
        self.route=validate_route(route);self.provider=provider;self.setter=setter;self.path=Path(journal)
        self.check_cancel=check_cancel;self.monotonic=monotonic;self.sleep=sleep;self.initial=None;self.current=None
        self.state={'schema':'qcal-bias-journal/1','policy':'f8_offset_only_measured_restore_v1',
            'identity':deepcopy(identity),'route':deepcopy(self.route),'state':'unopened','events':[]}
        self.touched=False;self.restoring=False;self.deadline=math.inf

    def _check(self):
        if not self.restoring:self.check_cancel()
        if self.monotonic()>=self.deadline:raise TimeoutError('Bias session bounded deadline exceeded')

    def _save(self,stage,**fields):
        self.state.update(state=stage,**fields);durable_save(self.path,self.state)

    def _wait(self,duration):
        end=self.monotonic()+duration
        while self.monotonic()<end:
            self._check();self.sleep(min(.05,end-self.monotonic()))
        self._check()

    def _read(self):
        self._check();nonce=uuid.uuid4().hex;before=self.monotonic()
        value=self.provider.snapshot(route=self.route,timeout_seconds=self.route['readTimeoutSeconds'],nonce=nonce)
        if self.monotonic()-before>self.route['readTimeoutSeconds']:raise TimeoutError('Readback provider exceeded timeout')
        self._check();snapshot=validate_snapshot(value,self.route,nonce)
        if self.initial and any(snapshot['outputState'].get(k)!=self.initial['outputState'].get(k) for k in ('kind','mode','outputEnabled','sharedSettingsSha256')):raise ValueError('Static output/shared settings changed during owned bias session')
        self.state['events'].append({'type':'readback','snapshot':snapshot});self.current=snapshot
        return snapshot

    def _stable(self,setting,measured_target=None):
        expected=target_output(self.route,setting) if measured_target is None else measured_target
        self._wait(self.route['settleSeconds']);samples=[]
        for index in range(self.route['stabilitySamples']):
            if index:self._wait(self.route['readbackIntervalSeconds'])
            sample=self._read();m=sample['measurement'];actual=sample['setting']['valueV']
            if abs(actual-setting)>self.route['toleranceV']/max(abs(self.route['offsetToOutput']['gain']),1):raise ValueError('Hardware setting readback differs from requested offset')
            uncertainty=m['uncertaintyV']+self.route['offsetToOutput']['uncertaintyV']
            if measured_target is not None:uncertainty+=self.initial['measurement']['uncertaintyV']
            if abs(m['valueV']-expected)+uncertainty>self.route['toleranceV']:raise ValueError('Measured bias outside target tolerance')
            samples.append(sample)
        values=[s['measurement']['valueV'] for s in samples]
        if max(values)-min(values)>self.route['toleranceV']:raise ValueError('Measured bias is not stable')
        if any(any(s['outputState'][k]!=samples[0]['outputState'][k] for k in ('kind','mode','outputEnabled','sharedSettingsSha256')) for s in samples):raise ValueError('Shared/static output state changed during stability measurement')
        return {**samples[-1],'stabilitySamples':samples,'requestedSettingV':setting,'expectedOutputV':expected}

    def open_and_read_initial(self):
        if self.path.exists():raise ValueError('Existing bias journal requires explicit reconciliation; no replay')
        self.deadline=self.monotonic()+self.route['maxRampSeconds']
        first=self._read();initial=self._stable(first['setting']['valueV'])
        self.initial=deepcopy(initial);self._save('prepared',initial=initial)
        return initial

    def _ramp(self,target,point_id,measured_target=None):
        number(target,'target offset',self.route['minimumV'],self.route['maximumV'])
        if self.initial is None:raise ValueError('Initial state must be verified before any write')
        # Fresh hardware register state, not last command/cache; essential after
        # a timeout with unknown command outcome.
        start=self._read()['setting']['valueV'];steps=math.ceil(abs(target-start)/self.route['maxStepV'])
        for index in range(1,steps+1):
            value=start+(target-start)*index/steps;previous=start+(target-start)*(index-1)/steps
            self._wait(abs(value-previous)/self.route['maxSlewVPerSecond']);self._check()
            self.touched=True;event={'type':'set_offset','pointId':point_id,'requestedV':value,'outcome':'unknown'}
            self.state['events'].append(event);self._save('restoring' if self.restoring else 'write_pending')
            before=self.monotonic()
            reply=self.setter(value,timeout_seconds=self.route['readTimeoutSeconds'])
            if self.monotonic()-before>self.route['readTimeoutSeconds']:raise TimeoutError('LF setter exceeded timeout')
            event.update(outcome='command_reply',reply=reply,replyIsMeasuredOutput=False)
            self._save('restoring' if self.restoring else 'ramping');self._check()
            current=self._read()
            if abs(current['setting']['valueV']-value)>1e-9:raise ValueError('Ramp command setting was not confirmed by fresh register readback')
        receipt=self._stable(target,measured_target);receipt['pointId']=point_id
        self._save('restoring' if self.restoring else 'held',lastVerified=receipt)
        return receipt

    def ramp_to(self,target,point_id):
        self.deadline=self.monotonic()+self.route['maxRampSeconds']
        return self._ramp(target,point_id)

    def verify_held(self,point_id):
        held=self.state.get('lastVerified')
        if not held or held['pointId']!=point_id:raise ValueError('No verified bias for this point')
        self.deadline=self.monotonic()+self.route['maxRampSeconds']
        receipt=self._stable(held['requestedSettingV']);receipt['pointId']=point_id
        self._save('held',lastVerified=receipt);return receipt

    def restore_initial(self,reason):
        if self.initial is None:return {'verified':not self.touched,'noWriteAttempted':not self.touched,'reason':reason}
        self.restoring=True;self.deadline=self.monotonic()+self.route['restoreTimeoutSeconds']
        try:
            self._save('restoring',restoreReason=reason)
            receipt=self._ramp(self.initial['setting']['valueV'],'restore_initial',self.initial['measurement']['valueV'])
            receipt.update(verified=True,reason=reason);self._save('restored',restoration=receipt);return receipt
        except BaseException as exc:
            receipt={'verified':False,'reason':reason,'error':str(exc)}
            self._save('needs_reconciliation',restoration=receipt);return receipt
        finally:self.restoring=False
