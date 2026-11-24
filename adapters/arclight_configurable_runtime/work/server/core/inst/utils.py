from dataclasses import dataclass

@dataclass(frozen=True)
class DriveTypeSchema:
    CY_6DA: str = "CY_6DA"
    CY_6DADC: str = "CY_6DADC"
    CY_10DA: str = "CY_10DA"
    CY_AD: str = "CY_AD"
    CY_SYNC: str = "CY_SYNC"
    Mocker: str = "Mocker"
    CeyearStation: str = "CeyearStation"
    # 7 号机：三台独立思仪设备，各自承担不同角色
    CeyearStationSync: str = "CeyearStationSync"
    CeyearStationXY: str = "CeyearStationXY"
    CeyearStationProb: str = "CeyearStationProb"