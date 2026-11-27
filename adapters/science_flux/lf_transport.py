"""Only the three reviewed native offset setters; never a guessed getter."""
import math
import threading
import time
from .contracts import validate_route,number,is_commanded

class NativeLFSetter:
    def __init__(self,handle,route,*,native=None):
        self.handle=handle;self.route=validate_route(route);self.mutex=threading.RLock()
        if native is None:
            from core.inst.driver_old import AWG1652AM as native
        self.native=native

    def __call__(self,value,*,timeout_seconds):
        number(value,'offset command',self.route['minimumV'],self.route['maximumV'])
        number(timeout_seconds,'LF timeout',.001,10)
        with self.mutex:
            previous=self.handle.timeout;deadline=time.monotonic()+timeout_seconds;replies=[]
            try:
                # Serialized on the sole owned handle; no other RF/ADC helper
                # runs concurrently with these stateful module/channel selects.
                for index,(function,args) in enumerate([(self.native.boardSelect,(self.route['board'],)),
                        (self.native.channSelect,(self.route['channel'],)),(self.native.setOffset,(value,'V'))]):
                    remaining=deadline-time.monotonic()
                    if remaining<=0:raise TimeoutError('Native LF transaction deadline expired')
                    self.handle.timeout=max(1,math.ceil(remaining*1000))
                    reply=function(self.handle,*args)
                    replies.append(reply if isinstance(reply,str) else {'invalidReplyType':type(reply).__name__})
                    if not isinstance(reply,str) or not reply.strip() or len(reply)>16384:raise ValueError('Invalid native setter command reply')
                    if is_commanded(self.route) and reply not in self.route['commandReplyContract'][('moduleReplies','channelReplies','offsetReplies')[index]]:
                        raise ValueError('Unknown or unsuccessful LF command reply; transaction aborted')
                if time.monotonic()>deadline:raise TimeoutError('Native LF transaction exceeded timeout')
                return {'nativeFunctions':['boardSelect','channSelect','setOffset'],'replies':replies,
                    'requestedSettingV':value,'unit':'V','measuredOutput':False}
            except BaseException as exc:
                # Preserve bounded raw partial/error replies without treating
                # them as ACKs or guessing whether an offset was applied.
                exc.native_transaction={'nativeFunctions':['boardSelect','channSelect','setOffset'],
                    'replies':[r[:16384] if isinstance(r,str) else r for r in replies],
                    'requestedSettingV':value,'unit':'V','measuredOutput':False,'commandConfirmed':False}
                raise
            finally:self.handle.timeout=previous
