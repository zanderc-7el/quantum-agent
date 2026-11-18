"""XY 驱动设备（7 号机）：全部为 RF 型板卡（每板 2 通道）。

板位与老系统 QOS 的 CeyearStationXY 一致。
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
    RF_boards = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]
    LO_boards = []
    OSC_boards = []
    CHs = list(itertools.product(LF_boards, (0, 1, 2, 3))) \
        + list(itertools.product(RF_boards, (0, 1))) \
        + list(itertools.product(OSC_boards, (0, 1)))

    RESET_OFFSET_LF_BOARDS = ([], (0, 1, 2, 3))
    RESET_OFFSET_RF_BOARDS = (RF_boards, (0, 1))
