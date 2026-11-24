"""采集/读出设备（7 号机）：ADC 采集板 + 读出 AWG。

板位与老系统 QOS 的 CeyearStationProb 一致：
  OSC_boards：采集板（每板 2 通道），对应 CH0~CH5
  RF_boards ：读出 AWG（每板 2 通道），对应 CY_R0~CY_R23
"""
import itertools
import logging

from .CeyearStation import CeyearStation as StationBase

log = logging.getLogger(__name__)


class Driver(StationBase):

    __log__ = log
    support_models = []

    # (board,chann) 板卡编号和通道编号组成的元组
    LF_boards = []
    RF_boards = [3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14]
    LO_boards = []
    OSC_boards = [0, 1, 2]
    CHs = list(itertools.product(LF_boards, (0, 1, 2, 3))) \
        + list(itertools.product(RF_boards, (0, 1))) \
        + list(itertools.product(OSC_boards, (0, 1)))

    RESET_OFFSET_LF_BOARDS = ([], (0, 1, 2, 3))
    RESET_OFFSET_RF_BOARDS = (RF_boards, (0, 1))
