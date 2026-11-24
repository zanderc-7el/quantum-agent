from dataclasses import dataclass, asdict, field
import numpy as np
import math
from enum import Enum
from typing import Any

@dataclass
class Wave:
    name: str
    srate: float          # Hz
    length_s: float       # seconds
    qids: list = field(default_factory=list)
    offset: float = 0
    use: bool = True
    start_at: float = 0
    data: np.ndarray = None
    fill: bool = False  # 是否填充0，用以判断波形是否从start_at开始输出

    def __post_init__(self):
        n = int(round(self.length_s * self.srate))
        self.data = np.zeros(n, dtype=float)

    @property
    def n(self):
        return self.data.size

    def get_data(self):
        if self.fill:
            return self.data
        else:
            return self.data[int(round(self.start_at * self.srate)):int(round(self.length_s * self.srate))]

    def get_t(self):
        """返回与 data 对齐的全局时间轴。"""
        return np.arange(self.n, dtype=float) / self.srate

    def mix(self, mrate: float = 0.0, phase: float = 0.0, iq: bool = False):
        """按全局时间轴把基带波形上变频。"""
        carrier = np.exp(1j * (2 * np.pi * mrate * self.get_t() + phase))
        mixed = self.data * carrier
        self.data = mixed if iq else mixed.real
        return self

    def add_segment(self, t0: float, t1: float, y: np.ndarray):
        """把 y 加到 [t0,t1) 对应的采样区间"""
        i0 = int(round(t0 * self.srate))
        i1 = int(round(t1 * self.srate))
        #print(self.name, i0, i1)
        if i1 <= i0:
            return
        i0 = max(i0, 0)
        i1 = min(i1, self.n, i0+len(y))
        n = i1 - i0
        if n <= 0:
            return
        # 动态类型提升：如果 y 是 complex，提升 self.data
        if np.issubdtype(y.dtype, np.complexfloating) and not np.issubdtype(self.data.dtype, np.complexfloating):
            self.data = self.data.astype(np.complex128)
            
        self.data[i0:i1] += y[:n]
        
    def to_dict(self):
        start_at = self.start_at
        if self.fill:
            start_at = 0
        return {
                "name": self.name,
                "srate": float(self.srate),
                "start_at": start_at,
                "length": self.data.size,
                "offset": self.offset,
                "segments": [{"start": 0, "data": self.data.tolist()}],
                "length_s": self.length_s
            }
    
    def to_sparse_dict(
        self,
    ):
        """
        把波形压缩成非零片段 segments: [{start:int, data:[...]}]
        """
        start_at = self.start_at
        if self.fill:
            start_at = 0
        y = self.data
        if np.issubdtype(self.data.dtype, np.complexfloating):
            y = y.real
        mask = np.abs(y) > 0
        if not np.any(mask):
            # 全零
            return {
                "name": self.name,
                "srate": float(self.srate),
                "start_at": start_at,
                "length": int(y.size),
                # "width": float(y.size / self.srate),
                "offset": self.offset,
                "segments": [],
                "length_s": self.length_s
            }

        idx = np.flatnonzero(mask)
        # 找连续区间：runs
        starts = [int(idx[0])]
        ends = []
        prev = int(idx[0])
        for k in idx[1:]:
            k = int(k)
            if k != prev + 1:
                ends.append(prev + 1)  # end is exclusive
                starts.append(k)
            prev = k
        ends.append(prev + 1)

        # 过滤短片段
        segs = [(s, e) for s, e in zip(starts, ends)]
        # 合并相近片段
        merge_gap = 10
        if merge_gap > 0 and len(segs) > 1:
            merged = []
            cs, ce = segs[0]
            for s, e in segs[1:]:
                if s - ce <= merge_gap:
                    ce = e
                else:
                    merged.append((cs, ce))
                    cs, ce = s, e
            merged.append((cs, ce))
            segs = merged

        segments_out = []
        
        for s, e in segs:
            chunk = y[s:e]
            segments_out.append({"start": int(s), "data": chunk.tolist()})

        out = {
            "name": self.name,
            "srate": float(self.srate),
            "start_at": start_at,
            "length": int(y.size),
            "width": float(y.size / self.srate),
            "offset": self.offset,
            "segments": segments_out,
            "length_s": self.length_s
        }
        return out


class WaveStore:
    def __init__(self, length_s: float):
        self.length_s = length_s
        self._waves: dict[str, Wave] = {}

    def wave(self, name: str, srate: float, qid=0, start_at=0, offset=0, fill=True) -> Wave:
        w = self._waves.get(name)
        if w is None:
            w = Wave(name=name, srate=srate, qids=[qid], length_s=self.length_s, start_at=start_at, offset=offset, fill=fill)
            self._waves[name] = w
        else:
            # 共用通道时必须保证采样率一致；不一致就应该报错或做重采样策略
            if abs(w.srate - srate) > 1e-9:
                raise ValueError(f"channel {name} srate mismatch: {w.srate} vs {srate}")
            if qid not in w.qids: w.qids.append(qid)
        return w

    def export(self) -> dict[str, np.ndarray]:
        return {k: v.data for k, v in self._waves.items()}
