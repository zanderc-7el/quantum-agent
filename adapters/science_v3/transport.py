"""Bounded native RF/ADC transport. No connection occurs at module import.

Tests inject native objects; production resolves only the fixed verified modules.
No plugin name, expression or transport import path comes from a request.
"""
from copy import deepcopy
import json
from pathlib import Path
import numpy as np
from .compiler import wave_identity

class NativeTransport:
    def __init__(self,plan,folder,check_cancel,*,native=None):
        self.plan=plan;self.folder=Path(folder);self.check_cancel=check_cancel
        if native is None:
            from core.inst import create_hardware
            from core.experiment.runner import SequenceRunner
            from core.inst.driver_old import Ceyear_OSC as OSC
            from cavity_runtime import close_resources
            from repeated_iq_acquisition import get_repeated_raw_data
            from platform_hooks import DelegatedLock
            native={'create_hardware':create_hardware,'runner':SequenceRunner,'osc':OSC,
                    'close_resources':close_resources,'acquire':get_repeated_raw_data,'delegated_lock':DelegatedLock}
        self.native=native;self.hardware=None;self.uploads=[];self.expected={};self.row_id=None

    def open(self):
        self.check_cancel();plan=self.plan;route=plan['routing'];p=plan['parameters'];instrument=plan['instrument']
        self.delegated_lock=self.native['delegated_lock']()
        self.delegated_lock.acquire({'operationId':plan['operationId'],'resourceId':plan['resourceId']})
        self.hardware=self.native['create_hardware']('CeyearStationF8','CeyearStationF8',instrument['address'],
            group='agent_f8',debug=False,visa_backend=plan['visaLibrary'],acquisition_timeout=20,timeout=10)
        hw=self.hardware
        for ch,key in [('RO','readoutLogical'),('AD','adcLogical'),('XY','xyLogical')]:hw.set_channel(ch,tuple(route[key]))
        hw.open();handle=hw.driver.handle;identity=handle.query('*IDN?').strip()
        self.native['osc'].oscboardSelect(handle,route['adcPhysical'][0])
        adc=float(self.native['osc'].oscSamRate(handle))*1e9
        if not np.isclose(adc,route['adcSampleRateHz'],rtol=0,atol=1):raise ValueError('Unexpected ADC clock')
        for key,value,ch in [('sampleRate',6e9,'RO'),('Amplitude',0,'RO'),('triggerDelay',0,'RO'),
                ('shots',p['shots'],'AD'),('triggerDelay',80e-6,'AD'),('Amplitude',p['xy_attenuation_db'],'XY'),
                ('triggerDelay',p['xy_delay_s'],'XY')]:hw.write(key,value,ch=ch)
        hw.driver.select_rf_filter(tuple(route['xyLogical']),p['xy_filter_mhz'])
        filter_hz=float(handle.query('AWG5000:SELFILTERFREQ?').strip())
        if filter_hz!=p['xy_filter_mhz']:raise ValueError('XY filter readback mismatch')
        original=hw.set_wave
        def guarded(wave,ch):
            self.check_cancel();actual=wave_identity(wave)
            if self.expected.get(wave.name)!=actual:raise ValueError('Upload bytes differ from compiled identity')
            result=original(wave,ch=ch)
            receipt={'rowId':self.row_id,'channel':ch,**actual,'uploadCommandsCompleted':True,'deviceWaveformReadback':False}
            self.uploads.append(receipt)
            with (self.folder/'uploads.jsonl').open('a',encoding='utf-8') as stream:stream.write(json.dumps(receipt,allow_nan=False)+'\n')
            return result
        hw.set_wave=guarded
        self.runner=self.native['runner']()
        return {'idn':identity,'adcSampleRateHz':adc,'route':route,'xyFilterMHz':filter_hz,'operationId':plan['operationId'],
                'resourceId':plan['resourceId'],'address':instrument['address'],'dcPolicy':'preserve_existing_dc'}

    def acquire(self,row,seq,expected):
        self.check_cancel();self.expected=deepcopy(expected);self.row_id=row['id'];before=len(self.uploads)
        p=self.plan['parameters']
        def block(record):
            if record.get('exact_duplicate_of_blocks'):raise ValueError('Exactly repeated ADC block detected')
            self.check_cancel()
        raw,index=self.native['acquire'](self.runner,seq,p['shots'],{'readout_mode':'IQ','sync':'CeyearStationF8',
            'group':'agent_f8','time_to_read':.08},acquisitions_per_point=p['acquisitions_per_point'],
            output_folder=self.folder,point_index=row['id'],on_block=block,return_block_index=True)
        i,q=np.asarray(raw[0]),np.asarray(raw[1]);shots=p['shots']*p['acquisitions_per_point']
        if i.shape!=(1,shots) or q.shape!=i.shape or list(raw[2])!=[1] or not np.isfinite(i+1j*q).all():
            raise ValueError('Invalid fresh native IQ dimensions or values')
        uploaded={r['name'] for r in self.uploads[before:]}
        if uploaded!=set(expected):raise ValueError('Missing successful XY/readout upload commands')
        np.savez_compressed(self.folder/f'point_{row["id"]:04d}.npz',i=i,q=q,acquisition_block_index=index)
        return i.copy(),q.copy()

    def close(self):
        if self.hardware is None:return {'shutdown_ok':True,'hardware_connected':False,'hardwareCreated':False}
        receipt=self.native['close_resources'](self.hardware,None)
        self.delegated_lock.release()
        return receipt
