"""Compose native RF/ADC transport with a separately journalled LF transaction."""
from adapters.science_v3.transport import NativeTransport
from .lf_transport import NativeLFSetter
from .contracts import timeout_for

class FluxTransport(NativeTransport):
    def __init__(self,plan,folder,check_cancel,*,native=None):
        super().__init__(plan,folder,check_cancel,native=native)
        self.identity_confirmed=False
        create=self.native['create_hardware']
        def create_guarded(*args,**kwargs):
            hardware=create(*args,**kwargs);open_handle=hardware.open
            def checked_open():
                result=open_handle()
                if hardware.driver.handle.query('*IDN?').strip()!=plan['biasRoute']['stationIdentity']:
                    raise ValueError('Connected station identity differs before RF/ADC configuration')
                self.identity_confirmed=True
                return result
            hardware.open=checked_open
            return hardware
        self.native={**self.native,'create_hardware':create_guarded}

    def open(self):
        result=super().open()
        if result.get('idn')!=self.plan['biasRoute']['stationIdentity']:raise ValueError('Connected station identity differs from confirmed bias route')
        self.set_offset=NativeLFSetter(self.hardware.driver.handle,self.plan['biasRoute'])
        return result

    def stop_rf(self):
        if not self.identity_confirmed:return {'verified':True,'noControlCommandsAttempted':True,'identityNotConfirmed':True,'physicalOutputReadback':False}
        if self.hardware is None or self.hardware.driver.handle is None:
            return {'verified':True,'noConnectedHandle':True,'physicalOutputReadback':False}
        handle=self.hardware.driver.handle;previous=handle.timeout
        try:
            handle.timeout=min(1000,max(1,int(timeout_for(self.plan['biasRoute'])*1000)))
            result=self.hardware.driver.cleanup_used_channels()
        finally:handle.timeout=previous
        ok=bool(result) and all(isinstance(v,dict) and v.get('ok') is True for v in result.values())
        return {'verified':ok,'commands':result,'physicalOutputReadback':False,
                'meaning':'Native RF/ADC/trigger stop commands completed; no physical RF output meter'}

    def close(self):
        if not self.identity_confirmed:
            # No configuration/trigger/output command was admitted. An unknown
            # endpoint must only have its new local connection closed, never
            # receive cleanup_used_channels (which closes global triggers).
            errors={};driver=self.hardware.driver if self.hardware is not None else None
            if driver is not None:
                for attr in ('handle','_resource_manager'):
                    resource=getattr(driver,attr,None)
                    if resource is not None:
                        try:resource.close();setattr(driver,attr,None)
                        except BaseException as exc:errors[attr]=str(exc)
                if not errors:
                    remove=getattr(self.hardware,'remove_instance',None)
                    if callable(remove):
                        try:remove(self.hardware)
                        except BaseException as exc:errors['registry']=str(exc)
            lock=getattr(self,'delegated_lock',None)
            if lock is not None:lock.release()
            return {'shutdown_ok':not errors,'noControlCommandsAttempted':True,'identityNotConfirmed':True,'shutdown_errors':errors}
        if self.hardware is not None and self.hardware.driver.handle is not None:
            self.hardware.driver.handle.timeout=min(1000,max(1,int(timeout_for(self.plan['biasRoute'])*1000)))
        return super().close()
