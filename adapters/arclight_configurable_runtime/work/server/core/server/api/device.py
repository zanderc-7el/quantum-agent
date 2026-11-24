from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from core.inst import *
from core.server.auth_deps import _auth_deps

router = APIRouter(prefix="/devices", tags=["devices"], dependencies=_auth_deps())

class AddReq(BaseModel):
    name: str
    cls: str
    address: str
    group: str
    sync_bind: str = None
    debug: bool = False

@router.post("/add", status_code=200)
def add_device(body: AddReq):
    
    try:
        dev = create_hardware(body.name, body.cls, body.address, group=body.group, sync_bind = body.sync_bind, debug = body.debug )
        return dev.get_status()
    except DeviceException as e:
        raise HTTPException(status_code=405, detail=e.msg)
    except Exception as e:
        raise HTTPException(status_code=405, detail=str(e))

class UpdateReq(BaseModel):
    name: str
    group: str
    address: str = None
    sync_bind: str = None
    
@router.post("/update", status_code=200)
def update_device(body: UpdateReq):
    
    try:
        dev = get_hardware(body.name, body.group)
        dev.modify(addr = body.address, sync_bind = body.sync_bind)
        return dev.get_status()
    except DeviceException as e:
        raise HTTPException(status_code=405, detail=e.msg)
    except Exception as e:
        raise HTTPException(status_code=405, detail=str(e))


class DevReq(BaseModel):
    name: str
    group: str
    
@router.post("/connect", status_code=200)
def connect_device(body: DevReq):
    
    try:
        dev = get_hardware(body.name, body.group)
        dev.open()
        return dev.get_status()
    except DeviceException as e:
        raise HTTPException(status_code=405, detail=e.msg)
    except Exception as e:
        raise HTTPException(status_code=405, detail=str(e))
    
@router.post("/close", status_code=200)
def close_device(body: DevReq):
    
    try:
        dev = get_hardware(body.name, body.group)
        dev.close()
        return dev.get_status()
    except DeviceException as e:
        raise HTTPException(status_code=405, detail=e.msg)
    except Exception as e:
        raise HTTPException(status_code=405, detail=str(e))

@router.post("/delete", status_code=200)
def delete_device(body: DevReq):
    
    try:
        dev = get_hardware(body.name, body.group)
        dev.delete()
        return dev.get_status()
    except DeviceException as e:
        raise HTTPException(status_code=405, detail=e.msg)
    except Exception as e:
        raise HTTPException(status_code=405, detail=str(e))

@router.post("/params", status_code=200)
def get_params(body: DevReq):
    
    try:
        dev = get_hardware(body.name, body.group)
        return dev.get_cache_param()
    except DeviceException as e:
        raise HTTPException(status_code=405, detail=e.msg)
    except Exception as e:
        raise HTTPException(status_code=405, detail=str(e))

@router.post("/param_specs", status_code=200)
def get_param_specs(body: DevReq):
    
    try:
        dev = get_hardware(body.name, body.group)
        specs =  dev.get_param_specs()
        return specs
    except DeviceException as e:
        raise HTTPException(status_code=405, detail=e.msg)
    except Exception as e:
        raise HTTPException(status_code=405, detail=str(e))

@router.post("/waves", status_code=200)
def get_waves(body: DevReq):
    try:
        dev = get_hardware(body.name, body.group)
        return dev.get_waveforms()
    except DeviceException as e:
        raise HTTPException(status_code=405, detail=e.msg)
    except Exception as e:
        raise HTTPException(status_code=405, detail=str(e))


@router.post("/status", status_code=200)
def get_status(body: DevReq):
    try:
        dev = get_hardware(body.name, body.group)
        #if not dev.is_busy(): 
        #    dev.get_info()
        return dev.get_status()
    except DeviceException as e:
        raise HTTPException(status_code=405, detail=e.msg)
    except Exception as e:
        raise HTTPException(status_code=405, detail=str(e))

class ChannelReq(BaseModel):
    dev_name: str
    group: str
    channel_name: str
    channel_id: Any=None
    
@router.post("/add_channel", status_code=200)
def add_channel(body: ChannelReq):
    try:
        dev:HardwareBase = get_hardware(body.dev_name, body.group)
        dev.set_channel(body.channel_name, body.channel_id)
        return dev.channel_map.channel_map
    except DeviceException as e:
        raise HTTPException(status_code=405, detail=e.msg)
    except Exception as e:
        raise HTTPException(status_code=405, detail=str(e))
    
@router.post("/remove_channel", status_code=200)
def remove_channel(body: ChannelReq):
    try:
        dev:HardwareBase = get_hardware(body.dev_name, body.group)
        dev.remove_channel(body.channel_name)
        return dev.channel_map.channel_map
    except DeviceException as e:
        raise HTTPException(status_code=405, detail=e.msg)
    except Exception as e:
        raise HTTPException(status_code=405, detail=str(e))
    

@router.post("/device_type", status_code=200)
def device_type():
    try:
        dev_type = list(HARDWARE_REGISTRY.keys())
        dev_type.sort()
        if "Mocker" in dev_type:
            dev_type.remove("Mocker")
        return {"device_type": dev_type}
    except DeviceException as e:
        raise HTTPException(status_code=405, detail=e.msg)
    except Exception as e:
        raise HTTPException(status_code=405, detail=str(e))



class TestReq(BaseModel):
    debug: bool
    group: str = "default"
    
@router.post("/test", status_code=200)
def load_device(body: TestReq):
    
    try:
        sync = InstrumentBase.find_instrument('SYNC') or create_hardware('SYNC', 'CY_SYNC', "169.254.164.100", group=body.group, debug=body.debug) 
        da = InstrumentBase.find_instrument('DA') or create_hardware('DA', 'CY_10DA', "169.254.164.70", group=body.group, sync_bind = "SYNC", debug=body.debug)
        ad = InstrumentBase.find_instrument('AD') or create_hardware('AD', 'CY_AD', "169.254.164.76", group=body.group, sync_bind = "SYNC", debug=body.debug)
        xy = InstrumentBase.find_instrument('XY') or create_hardware('XY', 'CY_6DA', "169.254.164.71", group=body.group, sync_bind = "SYNC", debug=body.debug)
        z = InstrumentBase.find_instrument('Z') or create_hardware('Z', 'CY_6DADC', "169.254.164.72", group=body.group, sync_bind = "SYNC", debug=body.debug)
        na = InstrumentBase.find_instrument('NA') or create_hardware("NA", "3656D", addr="xxxx", group=body.group, debug=body.debug)
        awg = InstrumentBase.find_instrument('AWG') or create_hardware("AWG", "CeyearStation", "127.0.0.1", group=body.group, debug=body.debug)
        #sync.open()
        #xy.open()
        #da.open()
        #ad.open()
        #z.open()
        
        da.set_channel("CH9", 9)
        ad.set_channel("CH9", 9)
        xy.set_channel("CH9", 9)
        z.set_channel("CH9", 9)
        na.set_channel("CH1", 1)
        
    except Exception as e:
        raise HTTPException(status_code=405, detail=str(e))
    
    return sync.get_status()