from copy import deepcopy
from typing import Union
import json
from numpy import pi

def deep_update(x, y):
    """
    递归合并两个字典，y 会覆盖 x 中相同的键
    对于字典值会递归合并，而不是直接替换
    """
    for key, value in y.items():
        if key in x and isinstance(x[key], dict) and isinstance(value, dict):
            deep_update(x[key], value)
        else:
            # 否则直接替换
            x[key] = value
    return x

def dotpath_to_dict(path, value):
    parts = path.split(".")
    d = {}
    cur = d
    for i, p in enumerate(parts):
        if i == len(parts) - 1:
            cur[p] = value
        else:
            cur[p] = {}
            cur = cur[p]
    return d

class SettingConfig:
    
    def __init__(self, raw_config: Union[dict, str]):
        self._cfg = raw_config
        if isinstance(raw_config, str):
            with open(raw_config, 'r', encoding='utf-8') as f:
                self._cfg = json.loads(f.read())
        
    def raw(self) -> dict:
        return self._cfg
    
    def clone(self):
        return SettingConfig(deepcopy(self._cfg))
    
    def apply_dict(self, patch: dict) -> None:
        # 递归合并 将字典patch中的内容跟新进来
        deep_update(self._cfg, patch)
        
    def apply_dotpath(self, path: str, value) -> None:
        patch = dotpath_to_dict(path, value)
        if "cfg" in patch:
            patch = patch["cfg"]
        self.apply_dict(patch)

    # ========= 获取设备组 =========
    
    # def dev_group(self):
    #     return self._cfg['Dev_Group']['name']

    # ========= 基础访问 =========
    
    def _q_key(self, q: int | tuple):
        if isinstance(q, tuple):
            a, b = q
            a, b = min(a, b), max(a, b)
            return self._cfg["Coupler"][f'Q{a}Q{b}']
        return f"Q{q}"
    
    def _q(self, q: int | tuple) -> dict:
        # 返回q-node的配置,q-node可能是Qi 也可能是QiQj 
        return self._cfg[self._q_key(q)]
    
    # ========= 全局时间参数 =========
    def global_wd_len(self) -> float:
        return self._cfg["ADC_Meas"]["global"]["timeSeq"]["wdLen"]
    
    def global_fill(self) -> bool:
        # tgqos 导出的芯片配置可能没有 fill 字段，默认 True（波形补零填充）
        return self._cfg["ADC_Meas"]["global"]["timeSeq"].get("fill", True)

    def global_prob_t0(self) -> float:
        return self._cfg["ADC_Meas"]["global"]["timeSeq"]["probT0"]
    
    def global_period(self) -> float:
        return self._cfg["ADC_Meas"]["global"]["timeSeq"]["period"]
    
    def global_gap_time(self) -> float:
        return self._cfg["ADC_Meas"]["global"]["timeSeq"]["gapTime"]
    
    # ========= XY gate =========
    def xy_pi_len(self, q) -> float:
        return self._q(q)["gate"]["U_gates"]["pi_len"]

    def xy_pi_amp(self, q) -> float:
        return self._q(q)["gate"]["U_gates"]["pi_amp"]

    def xy_shape(self, q) -> str:
        return self._q(q)["gate"]["U_gates"]["shape"]

    def xy_drag(self, q) -> bool:
        return self._q(q)["gate"]["U_gates"]["use_drag"]
    
    def xy_beta(self, q) -> float:
        return -1*self._q(q)["gate"]["U_gates"]["lambda1"] / (self._q(q)["gate"]["U_gates"]["Delta2"] * 2 * pi)

    def xy_srate(self, q) -> float:
        return self._q(q)["gate"]["sRate"]['XY']

    def xy_mrate(self, q) -> float:
        return self._q(q)["para"]["drivFreq"]
    
    def xy_channel(self, q) -> str:
        return self._q(q)['inst']['xyGateChI']
    
    def xy_buff(self, q) -> float:
        return self._q(q)["gate"]["U_gates"]["buff"]
    
    def z_srate(self, q) -> float:
        return self._q(q)["gate"]['sRate']['Z']

    def z_amp(self, q) -> float:
        return self._q(q)["gate"]["U_gates"]["z_amp"]
    
    def flux_value(self, q) -> float:
        return self._q(q)["para"]["fluxValue"]
    
    def z_channel(self, q) -> str:
        return self._q(q)['inst']['fluxCH']
    
    # ========= Z pulse =========
    def z_pulse_width(self, q) -> float:
        return self._q(q)["gate"]["Zpulse_gates"]["width"]
    
    def z_pulse_amp(self, q) -> float:
        return self._q(q)["gate"]["Zpulse_gates"]["amp"]
    
    # ========= CZ gate =========
    
    def cz_width(self, q):
        return self._q(q)["gate"]['CZ']['width']

    def cz_amp(self, q):
        return self._q(q)["gate"]['CZ']['amp']
    
    def z_shape(self, q):
        return self._q(q)["gate"]['Zpulse_gates']['shape']

        
    # ========= 读出/ADC profile 选择 =========
    def adc_prob_key(self, q):
        # Q{q}.inst.ADC_PROB
        return self._q(q)["inst"]["ADC_PROB"]

    def _adc_profile(self, q) -> dict:
        key = self.adc_prob_key(q)
        return self._cfg["ADC_Meas"][key]["para"]

    # ========= 读出脉冲参数 =========
    def da_width(self, q) -> float:
        return self._adc_profile(q)["width"]

    def da_pre_width(self, q) -> float:
        return self._adc_profile(q)["shape_kw"]["pre_width"]

    def da_amp(self, q) -> float:
        return self._q(q)["para"]["probCarryScale"]
    
    def da_factor(self, q) -> float:
        return self._adc_profile(q)["shape_kw"]["factor"]

    def da_srate(self, q) -> float:
        srate = self._adc_profile(q)["sRate"]
        if abs(srate - 6e9) < 1e-6:
            srate = 2e9
        return srate

    def da_mrate(self, q) -> float:
        srate = self._adc_profile(q)["sRate"]
        mrate = self._q(q)["para"]["probFreq"]
        if abs(srate - 6e9) < 1e-6:
            mrate -= 7e9
        return mrate
    
    def da_channel(self, q) -> str:
        key = self.adc_prob_key(q)
        return self._cfg["ADC_Meas"][key]["inst"]['probChI']
    
    # ========= AD 参数 =========
    
    def _ad_cfg(self, q) -> dict:
        key = self.adc_prob_key(q)
        return self._cfg["ADC_Meas"][key]["Multi_Readout"]
    
    def ad_channel(self, q) -> str:
        key = self.adc_prob_key(q)
        return self._cfg["ADC_Meas"][key]["inst"]['ADCCh']
    
    def ad_freq(self, qids):
        freq_list = []
        for q in qids:
            freq = self._q(q)["para"]["probCarryFreq"]
            freq_list.append(freq)
        return freq_list
        
    def ad_phase(self, qids):
        phase_list = []
        for q in qids:
            phase = self._q(q)["para"]["demodPhase"]
            phase_list.append(phase)
        return phase_list
    
    def ad_point_start(self, qids):
        point_start_list = []
        for q in qids:
            point_start = self._q(q)["para"]["demodPointStart"]
            point_start_list.append(point_start)
        return point_start_list 
    
    def ad_point_len(self, qids):
        point_len_list = []
        for q in qids:
            point_len = self._q(q)["para"]["demodPointLen"]
            point_len_list.append(point_len)
        return point_len_list
    
    def ad_point_num(self, q):
        return self._adc_profile(q)['pointNum']
    
    def ad_delay(self, q) -> float:
        return self._adc_profile(q)["triggerDelay"]
    
    # ========= NA 参数 =========
    
    def _na_profile(self):
        return self._cfg['NA_Meas']['para']
    
    def na_power(self):
        return self._na_profile()['power']
    
    def na_bandwidth(self):
        return self._na_profile()['bandwidth']
    
    def na_center(self):
        return self._na_profile()['center']
    
    def na_span(self):
        return self._na_profile()['span']
    
    def na_start(self):
        return self._na_profile()['start']
    
    def na_stop(self):
        return self._na_profile()['stop']
    
    def na_points(self):
        return self._na_profile()['points']
    
    def na_format(self):
        return self._na_profile()['format']
    
    def na_s_type(self):
        return self._na_profile()['s_type']
    
    def na_channel(self):
        return self._cfg['NA_Meas']['NA']
    
