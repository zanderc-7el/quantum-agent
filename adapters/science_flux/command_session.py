"""Bounded actual LF writes with declared start/end; unknown writes quarantine."""
from copy import deepcopy
import datetime as dt
import math
from pathlib import Path
import time
import uuid
from .bias_session import durable_save
from .command_contracts import POLICY,validate_command_route,validate_authorization,validate_command_receipt
from .contracts import number
from adapters.science_runtime.policy import digest

def now():return dt.datetime.now(dt.timezone.utc).isoformat()

class CommandedBiasSession:
    def __init__(self,route,provider,setter,journal,*,identity,authorization,check_cancel,monotonic=time.monotonic,sleep=time.sleep):
        if provider is not None:raise ValueError('Command policy does not use a measured provider')
        self.route=validate_command_route(route);self.identity=deepcopy(identity)
        self.authorization=validate_authorization(authorization,self.route,identity)
        if self.authorization['stage']=='plan_only':raise ValueError('Budget entry assumptions cannot authorize LF writes')
        self.setter=setter;self.path=Path(journal);self.check_cancel=check_cancel;self.monotonic=monotonic;self.sleep=sleep
        self.initial=None;self.current=None;self.touched=False;self.unknown=False;self.ending=False;self.deadline=math.inf
        self.state={'schema':'qcal-commanded-bias-journal/1','policy':POLICY,'identity':self.identity,
            'route':deepcopy(self.route),'authorization':self.authorization,'state':'unopened','events':[]}

    def _save(self,stage,**fields):self.state.update(state=stage,**fields);durable_save(self.path,self.state)
    def _check(self):
        if not self.ending:self.check_cancel()
        if self.monotonic()>=self.deadline:raise TimeoutError('Commanded bias deadline exceeded')
    def _wait(self,duration):
        end=self.monotonic()+duration
        while self.monotonic()<end:self._check();self.sleep(min(.05,end-self.monotonic()))
        self._check()
    def _write(self,value,point_id,delay=0.):
        self._check();number(value,'command voltage',self.route['minimumV'],self.route['maximumV'])
        command={'schema':'qcal-lf-command/1','id':uuid.uuid4().hex,**self.identity,'routeSha256':digest(self.route),
            'pointId':point_id,'previousCommandedV':self.current['valueV'] if self.current else self.authorization['entrySettingV'],
            'valueV':value,'issuedAt':now(),'delaySeconds':delay}
        event={'type':'set_offset','valueV':value,'commandId':command['id'],'outcome':'unknown','command':deepcopy(command)}
        self.state['events'].append(event);self._save('end_write_pending' if self.ending else 'write_pending')
        self.touched=True;self.unknown=True;before=self.monotonic()
        try:
            reply=self.setter(value,timeout_seconds=min(self.route['commandTimeoutSeconds'],max(.001,self.deadline-before)))
            event['replyEvidence']=deepcopy(reply)
            if self.monotonic()-before>self.route['commandTimeoutSeconds']:raise TimeoutError('LF command exceeded bounded timeout')
        except BaseException as exc:
            event.update(error=str(exc),replyEvidence=deepcopy(getattr(exc,'native_transaction',event.get('replyEvidence'))))
            self._save('write_outcome_unknown');raise
        command.update(completedAt=now(),elapsedSeconds=self.monotonic()-before,reply=deepcopy(reply));command['sha256']=digest(command)
        # A reply must match the confirmed success-token contract. It never
        # substitutes for a physical terminal-voltage measurement.
        provisional=self._receipt(command,point_id,self.route['settleSeconds'])
        validate_command_receipt(provisional,self.route,identity=self.identity,authorization_value=self.authorization)
        event.update(outcome='transport_reply',command=command);self.current=command;self.unknown=False
        self._save('ending' if self.ending else 'commanded');self._check()
        return command

    def _receipt(self,command,point_id,elapsed):
        return {'schema':'qcal-commanded-bias-receipt/1','policy':POLICY,'pointId':point_id,
            'requestedSettingV':command['valueV'],'setting':{'kind':'commanded_setpoint','valueV':command['valueV']},
            'measurement':None,'measuredOutputV':None,'readbackStatus':'not_measured','physicalBiasVerified':False,
            'command':deepcopy(command),'observedAt':now(),'authorization':deepcopy(self.authorization),'authorizationSha256':digest(self.authorization),
            'settle':{'seconds':self.route['settleSeconds'],'elapsedSeconds':elapsed,'stableReadbackVerified':False}}

    def _held(self,point_id):
        if self.current is None or self.unknown:raise ValueError('Unknown LF state cannot authorize acquisition')
        before=self.monotonic();self._wait(self.route['settleSeconds'])
        receipt=self._receipt(self.current,point_id,max(self.route['settleSeconds'],self.monotonic()-before))
        validate_command_receipt(receipt,self.route,identity=self.identity,authorization_value=self.authorization)
        self._save('ending' if self.ending else 'held',lastCommanded=receipt);return receipt

    def open_and_read_initial(self):
        # Interface name retained for worker compatibility; no read occurs.
        if self.path.exists():raise ValueError('Existing command journal requires reconciliation; no replay')
        self.deadline=self.monotonic()+self.route['maxRampSeconds']
        self._save('authorized_start',initialBasis={'kind':'authorized_command_coordinate','valueV':self.authorization['entrySettingV'],
            'evidence':deepcopy(self.authorization['entryEvidence']),'physicalReadback':False})
        # This is a declared or continuously locked prior command coordinate,
        # not a fresh device reading. Never jump directly from end to start.
        self.current={'valueV':self.authorization['entrySettingV']}
        self.initial=self._ramp(self.route['startSettingV'],'authorized_start')
        self._save('prepared',initial=self.initial);return self.initial

    def ramp_to(self,target,point_id):
        self.deadline=self.monotonic()+self.route['maxRampSeconds'];return self._ramp(target,point_id)
    def _ramp(self,target,point_id):
        number(target,'target command',self.route['minimumV'],self.route['maximumV'])
        if self.current is None or self.unknown:raise ValueError('Cannot ramp from an unknown command outcome')
        start=self.current['valueV'];steps=max(1,math.ceil(abs(target-start)/self.route['maxStepV']))
        for index in range(1,steps+1):
            value=target if index==steps else start+(target-start)*index/steps
            delay=abs(value-self.current['valueV'])/self.route['maxSlewVPerSecond']
            self._wait(delay);self._write(value,point_id,delay)
        return self._held(point_id)
    def verify_held(self,point_id):
        if self.state.get('lastCommanded',{}).get('pointId')!=point_id:raise ValueError('No commanded point for this acquisition')
        self.deadline=self.monotonic()+self.route['maxRampSeconds'];return self._held(point_id)
    def restore_initial(self,reason):
        # Explicitly returns to the declared end, NOT to an inferred initial
        # physical output. Acquisition STOP cannot consume the cleanup reserve.
        self.ending=True;self.deadline=self.monotonic()+self.route['endTimeoutSeconds']
        try:
            if self.unknown:raise ValueError('Uncertain write outcome; no blind end ramp without readback')
            if not self.touched:
                end={'verified':False,'commandEndVerified':True,'noWriteAttempted':True,'reason':reason,'physicalBiasVerified':False}
                self._save('no_write_attempted',restoration=end);return end
            end=self._ramp(self.route['endSettingV'],'declared_end')
            end.update(verified=False,commandEndVerified=True,endPolicy='return_declared_setting',reason=reason,
                       initialPhysicalStateRestored=False)
            self._save('returned_to_declared_setting',restoration=end);return end
        except BaseException as exc:
            end={'verified':False,'commandEndVerified':False,'reason':reason,'error':str(exc),'physicalBiasVerified':False}
            self._save('needs_reconciliation',restoration=end);return end
        finally:self.ending=False
