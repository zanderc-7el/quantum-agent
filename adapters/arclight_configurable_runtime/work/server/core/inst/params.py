from dataclasses import dataclass, asdict
from typing import Optional, Deque, Any, Dict, List, Callable


@dataclass(frozen=True)
class ParamSpec:
    # write() 用的 name
    key: str
    doc: str = ""
    # 值校验/变换：val -> val
    cast: Optional[Callable[[Any], Any]] = None
    # 是否需要 channel 参数（用于 {ch} 模板）
    requires_ch: bool = False
    value: Optional[Any] = None
    use_cache: bool = True
    
CY_6DA_PARAMS = {
    "delay": ParamSpec("TriggerDelayAWG", "触发延迟", float, requires_ch=True, value=0),
    "rdatt": ParamSpec("rdAtt", "通道衰减", float, requires_ch=True, value=0),
    "nyzone": ParamSpec("Nyzone", "奈奎斯特区间", int, value=2),
    "feedback": ParamSpec("FeedBack", "反馈使能", int, value=0),
    "cawen": ParamSpec("CAWEn", "载波使能", int, value=0),
    "cycle_seq": ParamSpec("CycleSeq", "播放模式", int, value=1),
}

CY_10DA_PARAMS = {
    "delay": ParamSpec("TriggerDelayAWG", "触发延迟", float, requires_ch=True, value=0),
    "rdatt": ParamSpec("rdAtt", "通道衰减", float, requires_ch=True, value=0),
    "nyzone": ParamSpec("Nyzone", "奈奎斯特区间", int, value=2),
    "feedback": ParamSpec("FeedBack", "反馈使能", int, value=0),
    "cawen": ParamSpec("CAWEn", "载波使能", int, value=0),
    "cycle_seq": ParamSpec("CycleSeq", "播放模式", int, value=1),
}

CY_6DADC_PARAMS = {
    "delay": ParamSpec("TriggerDelayAWG", "触发延迟", float, requires_ch=True, value=0),
    "offset": ParamSpec("offset", "直流偏置", float, requires_ch=True, value=0),
    "nyzone": ParamSpec("Nyzone", "奈奎斯特区间", int, value=2),
    "gain": ParamSpec("Gain", "增益参考路径", int, requires_ch=True, value=0),
    "cawen": ParamSpec("CAWEn", "载波使能", int, value=0),
    "cycle_seq": ParamSpec("CycleSeq", "播放模式", int, value=1),
}

CY_AD_PARAMS = {
    "delay": ParamSpec("TriggerDelay", "触发延迟", float, requires_ch=True, value=0),
    "rdatt": ParamSpec("rdAtt", "通道衰减", float, requires_ch=True, value=0),
    "trig_type": ParamSpec("TrigType", "触发类型", int, value=0),
    "start_cap": ParamSpec("StartCapture", "采集使能", value=0, use_cache=False),
    "shot": ParamSpec("Shot", "shot数目", int, requires_ch=True, value=1),
    "point_num": ParamSpec("pointNum", "采集段长", int, requires_ch=True, value=1),
    "coeff": ParamSpec("Coefficient", "解模系数", requires_ch=True, value=[]),
    "acq_info": ParamSpec("Acqinfo", "中间测量参数", requires_ch=True, value=[]),
}

CY_NA_PARAMS = {
    "power": ParamSpec("Power", "功率大小", float, requires_ch=True, value=-20),
    "bandwidth": ParamSpec("Bandwidth", "带宽", float, requires_ch=True, value=1000),
    "center": ParamSpec("Frequency center", "中心频率", float, requires_ch=True, value=5e9, use_cache=False),
    "span": ParamSpec("Frequency span", "功率跨度", float, requires_ch=True, value=2e9, use_cache=False),
    "start": ParamSpec("Frequency start", "功率扫描起始点", float, requires_ch=True, value=4e9, use_cache=False),
    "stop": ParamSpec("Frequency stop", "功率扫描结束点", float, requires_ch=True, value=6e9, use_cache=False),
    "points": ParamSpec("Number of points", "功率扫描点数", int, requires_ch=True, value=201, use_cache=False),
    "format": ParamSpec("Format", "返回数据格式", str, requires_ch=True, value="Mlog"),
    "s_type": ParamSpec("S_type", "测量参数定义", str, requires_ch=True, value="S21"),
}

CY_OLD_PARAMS = {
    "delay": ParamSpec("triggerDelay", "触发延迟", float, requires_ch=True, value=0),
    "offset": ParamSpec("Offset", "直流偏置", float, requires_ch=True, value=0),
    "start_cap": ParamSpec("StartCapture", "采集使能", requires_ch=True, value=0, use_cache=False),
    "shot": ParamSpec("shots", "shot数目", int, requires_ch=True, value=1),
    "point_num": ParamSpec("pointNum", "采集段长", int, requires_ch=True, value=1),
    "coeff": ParamSpec("Coefficient", "解模系数", requires_ch=True, value=[]),
}

# 单机版思仪站：波形输出 + 采集（6 号机一体化设备）
# 7 号机三台独立设备：Sync（Z/同步）与 XY（XY 驱动）只需波形输出参数
CY_STATION_DRIVE_PARAMS = {
    "delay": ParamSpec("triggerDelay", "触发延迟", float, requires_ch=True, value=0),
    "offset": ParamSpec("Offset", "直流偏置", float, requires_ch=True, value=0),
}