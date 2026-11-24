from dataclasses import dataclass
from typing import Any, Dict, List, Optional
import time
import numpy as np
from ..inst import get_hardware
from .utils import ChannelTypeSchema, MetaKeySchema

def get_dev_and_ch(qid, config, type=ChannelTypeSchema.xy, group='default'):
    """获取端口"""
    
    
    if type == ChannelTypeSchema.xy:
        dev_ch = config.xy_channel(qid)
    if type == ChannelTypeSchema.z:
        dev_ch = config.z_channel(qid)
    if type == ChannelTypeSchema.ro:
        dev_ch = config.da_channel(qid)
    if type == ChannelTypeSchema.ad:
        dev_ch = config.ad_channel(qid)
    if type == ChannelTypeSchema.na:
        dev_ch = config.na_channel()
    
    dev = get_hardware(dev_ch.split('.')[0], group=group)
    ch = dev_ch.split('.')[1]
    return dev, ch
    
def get_IQ_data(raw, shots, measure_list):
    I_data_dict = {}
    Q_data_dict = {}
    for (I, Q), qids in raw:
        I = np.array(I).T
        Q = np.array(Q).T
        for i, qid in enumerate(qids):
            I_data_dict[qid] = I[i]
            Q_data_dict[qid] = Q[i]
    I_data = []
    Q_data = []
    for qid in measure_list:
        I_data.append(I_data_dict[qid])
        Q_data.append(Q_data_dict[qid])
    return np.vstack(I_data), np.vstack(Q_data)

class SequenceRunner:
    """
    通用线路运行器：负责把 seq 跑起来，拿回 raw data
    """

    def get_raw_data(self, seq, shots: int, meta: Dict[str, Any]) -> Any:
        """
        运行线路，获取原始数据
        """
        cfg = seq.config
        read_mode = meta.get(MetaKeySchema.readout_mode, 'IQ')
        time_to_read = meta.get(MetaKeySchema.time_to_read, 0)
        group = meta.get(MetaKeySchema.group, "default")
        sync = get_hardware(meta.get(MetaKeySchema.sync_inst, 'SYNC'), group=group)
        
        measure_list = seq.measure_list
        # 0）获取脉冲波形数据
        seq.perform_sequence_awg()
        
        # 1) 配置并下发波形（按 seq.waves 扫一遍）
        ad_chs = []
        for k in seq.waves._waves:
            w = seq.waves._waves[k]
            q = w.qids[0]
            ch_type = w.name.split('_')[0]
            if w.name.startswith(ChannelTypeSchema.xy) and w.use:
                dev, ch = get_dev_and_ch(q, cfg, type=ch_type, group=group)
                dev.set_wave(w, ch=ch)
            elif w.name.startswith(ChannelTypeSchema.z):
                dev, ch = get_dev_and_ch(q, cfg, type=ch_type, group=group)
                dev.set_offset(w.offset, ch=ch)
                if w.use:
                    dev.set_wave(w, ch=ch) 
            elif w.name.startswith(ChannelTypeSchema.ro) and w.use:
                dev, ch = get_dev_and_ch(q, cfg, type=ch_type, group=group)
                dev.set_wave(w, ch=ch)
                if not w.fill:
                    delay = cfg.global_prob_t0()
                    dev.set_delay(delay, ch=ch)
                coeff = [cfg.ad_freq(w.qids),
                        cfg.ad_phase(w.qids),
                        cfg.ad_point_start(w.qids),
                        cfg.ad_point_len(w.qids),
                        [0,0,0],[0,0,0]]
                ad_dev, ad_ch = get_dev_and_ch(q, cfg, type=ChannelTypeSchema.ad, group=group)
                delay = cfg.ad_delay(q)
                ad_dev.set_delay(delay, ch=ad_ch)
                pointNum = cfg.ad_point_num(q)
                ad_dev.set_shot(shots, ch=ad_ch)
                ad_dev.set_point_num(pointNum, ch=ad_ch)
                ad_dev.set_coeff(coeff, ch=ad_ch)
                
                ad_dev.set_start_cap(0, ch=ad_ch)
                ad_chs.append((ad_dev, ad_ch, w.qids))
        
        # 2) 触发运行 + 采集
        period = cfg.global_period()
        sync.set_trigger(shots, period=period)
        
        if time_to_read > 0: time.sleep(time_to_read)
        
        # 3) 读回数据
        raw_data = []
        for dev, ch, qids in ad_chs:
            ch_data = dev.read(read_mode, ch=ch)
            if not ch_data:
                raise RuntimeError(
                    f"设备 {dev.name} 通道 {ch} 未返回 {read_mode} 数据，"
                    f"请检查该设备型号与通道映射是否匹配（通道ID需为该设备实际的 (板卡, 通道)）"
                )
            raw_data.append((ch_data, qids))

        if read_mode == 'IQ':
            I_data, Q_data = get_IQ_data(raw_data, shots, measure_list)
            return [I_data, Q_data, measure_list]
        else:
            return raw_data


class VNARunner:
    """
    网分运行器
    """

    def get_raw_data(self, cfg, meta, scan_mode = 0) -> Any:
        """
        运行线路，获取原始数据
        """
        read_mode = meta.get(MetaKeySchema.readout_mode, 'S')
        q = meta.get(MetaKeySchema.qubit, 0)
        time_to_read = meta.get(MetaKeySchema.time_to_read, 1)
        group = meta.get(MetaKeySchema.group, "default")
        
        # 1) VNA配置
        na, ch = get_dev_and_ch(0, cfg, ChannelTypeSchema.na, group=group)
        na.set_power(cfg.na_power(), ch=ch)
        na.set_bandwidth(cfg.na_bandwidth(), ch=ch)
        na.set_points(cfg.na_points(), ch=ch)
        na.set_format(cfg.na_format(), ch=ch)
        na.set_s_type(cfg.na_s_type(), ch=ch)
        if scan_mode == 0:
            na.set_center(cfg.na_center(), ch=ch)
            na.set_span(cfg.na_span(), ch=ch)
        else:
            na.set_start(cfg.na_start(), ch=ch)
            na.set_stop(cfg.na_stop(), ch=ch)
        # 2) 磁通配置
        #z_dev, z_ch = get_dev_and_ch(q, cfg, ChannelTypeSchema.z)
        #offset = cfg.flux_value(q)
        #z_dev.set_offset(offset, ch=z_ch)
        # 3）读取结果
        raw_data = na.read(read_mode, ch=ch)        
        
        time.sleep(time_to_read)
        
        return raw_data
