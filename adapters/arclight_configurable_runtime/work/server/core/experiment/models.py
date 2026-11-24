from pydantic import BaseModel, Field
from typing import Dict, List, Literal, Optional, Any
import time, uuid
from dataclasses import dataclass

@dataclass(frozen=True)
class Status:
    queued: str = "queued"
    running: str = "running"
    done: str = "done"
    error: str = "error"
    canceled: str = "canceled"
    
class Progress(BaseModel):
    job_id: str
    status: str
    total_points: int = 0
    done_points: int = 0
    current_axes: Dict[str, Any] = {}
    throughput_pts_per_s: float = 0.0
    eta_sec: Optional[float] = None
    shots_total: int = 0
    shots_done: int = 0
    started_at: Optional[float] = None
    msg: Optional[str] = None
    traceback: Optional[str] = None
    run_type: Optional[str] = None
    sweep_list: list = []
    expand_mode: str = 'cartesian'
    updated_at: float = 0.0
    group: str = 'default'

class Chunk(BaseModel):
    axes: Dict[str, float]
    result: Any

def new_job_id() -> str:
    return str(uuid.uuid4())
