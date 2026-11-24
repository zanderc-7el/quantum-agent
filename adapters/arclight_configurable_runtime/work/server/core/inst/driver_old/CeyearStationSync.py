"""同步设备（7 号机）：Z 通道 + 触发主设备。

板位与老系统 QOS 的 CeyearStationSync 一致：全部为 LF 型板卡（每板 4 通道）。
"""
import itertools
import logging

from .CeyearStation import CeyearStation as StationBase

log = logging.getLogger(__name__)


class Driver(StationBase):

    __log__ = log
    support_models = []

    # (board,chann) 板卡编号和通道编号组成的元组
    LF_boards = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]
    RF_boards = []
    LO_boards = []
    OSC_boards = []
    CHs = list(itertools.product(LF_boards, (0, 1, 2, 3))) \
        + list(itertools.product(RF_boards, (0, 1))) \
        + list(itertools.product(OSC_boards, (0, 1)))

    RESET_OFFSET_LF_BOARDS = (LF_boards, (0, 1, 2, 3))
    RESET_OFFSET_RF_BOARDS = ([], (0, 1))
