from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from core.config import Global_Setting
from core.experiment import *
from core.server.auth_deps import _auth_deps
from typing import Dict, List, Any, Union, Annotated, Optional
import asyncio
import os
import h5py
import json
import numpy as np

router = APIRouter(prefix="/exp", tags=["exp"], dependencies=_auth_deps())


class ExpFile(BaseModel):
    exp_file: str
    setting_file: str
    axes: dict = None

@router.post("/pic", status_code=200)
def get_pic(body: ExpFile):
    
    if body.axes is None:
        raise HTTPException(status_code=405, detail=f"获取图片失败: 未提供扫描参数")
    
    try:
        with ScanExperiment.load(body.setting_file, body.exp_file) as exp:
            seq = exp.get_sequence(axes=body.axes)
            seq.perform_sequence_awg()
            seq_base64 = plot_sequence(seq.pulse_sequence, remap_time=True, to_base64=True)
            return {"sequence_img": seq_base64,
                    "wave_data": {k: v.to_sparse_dict() for k, v in seq.waves._waves.items() if v.use}}
    except Exception as e:
        raise HTTPException(status_code=405, detail=f"获取图片失败: {str(e)}")

_jobs: Dict[str, Dict[str, Any]] = {}  # job_id -> {progress, task, cancel, exp}

def _job_h5_path(job_id: str) -> str:
    return os.path.join(Global_Setting.get_datapath(), f"{job_id}.hdf5")

def _job_log_path(job_id: str) -> str:
    return os.path.join(Global_Setting.get_logpath(), f"{job_id}.log")


def _read_h5_value(node):
    if isinstance(node, h5py.Dataset):
        value = node[()]
        if isinstance(value, bytes):
            return value.decode("utf-8")
        arr = np.asarray(value)
        if arr.shape == ():
            return arr.item()
        return arr.tolist()

    data = {key: _read_h5_value(node[key]) for key in node.keys()}
    attrs = {}
    for key, value in node.attrs.items():
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        attrs[key] = value
    return {"attrs": attrs, "children": data}


def _jsonable_h5_value(value):
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    return value


def _read_h5_field(group, name: str, default=None):
    if name in group:
        return _read_h5_value(group[name])
    if name in group.attrs:
        return _jsonable_h5_value(group.attrs[name])
    return default


def _h5_ordered_keys(group):
    return sorted(
        group.keys(),
        key=lambda key: (0, int(key)) if str(key).isdigit() else (1, str(key)),
    )


def _read_plot_line_group(group):
    line = {
        "type": _jsonable_h5_value(group.attrs.get("type", "")),
        "legend": _jsonable_h5_value(group.attrs.get("legend", None)),
    }
    if "x" in group or "x" in group.attrs:
        line["x"] = _read_h5_field(group, "x")
    if "y" in group or "y" in group.attrs:
        line["y"] = _read_h5_field(group, "y")
    return line


def _read_plot_object_group(group):
    obj_type = _jsonable_h5_value(group.attrs.get("type", ""))
    if obj_type == "plot":
        lines_group = group.get("lines")
        lines = []
        if isinstance(lines_group, h5py.Group):
            lines = [
                _read_plot_line_group(lines_group[key])
                for key in _h5_ordered_keys(lines_group)
            ]
        return {
            "type": "plot",
            "title": _jsonable_h5_value(group.attrs.get("title", "")),
            "xLabel": _jsonable_h5_value(group.attrs.get("xLabel", "")),
            "yLabel": _jsonable_h5_value(group.attrs.get("yLabel", "")),
            "xPoints": _read_h5_field(group, "xPoints"),
            "lines": lines,
        }

    if obj_type == "imshow":
        return {
            "type": "imshow",
            "title": _jsonable_h5_value(group.attrs.get("title", "")),
            "xLabel": _jsonable_h5_value(group.attrs.get("xLabel", "")),
            "yLabel": _jsonable_h5_value(group.attrs.get("yLabel", "")),
            "zLabel": _jsonable_h5_value(group.attrs.get("zLabel", "")),
            "xList": _read_h5_field(group, "xList"),
            "yList": _read_h5_field(group, "yList"),
            "data": _read_h5_field(group, "data"),
        }

    return _read_h5_value(group)


def _read_analysis_group(group):
    analysis_type = _jsonable_h5_value(group.attrs.get("type", ""))
    if analysis_type == "plot_list":
        return [
            _read_plot_object_group(group[key])
            for key in _h5_ordered_keys(group)
        ]
    if analysis_type in ("plot", "imshow"):
        return [_read_plot_object_group(group)]
    return _read_h5_value(group)


@router.post("/start", status_code=200)
async def start_scan(body: ExpFile):
    
    try:
        job_id = new_job_id()
        exp = Experiment.load(body.setting_file, body.exp_file, exp_id=job_id)
        progress = Progress(job_id=job_id, status="queued") 
        cancel_flag = {"stop": False}
            
        def should_stop(): 
            return cancel_flag["stop"]
            
        _jobs[job_id] = {
            "progress": progress,
            "task": None,
            "cancel": cancel_flag,
            "exp": exp,
        }
            
        def _done_cb(t: asyncio.Task, jid=job_id):
            job = _jobs.get(jid)
            _jobs.pop(jid, None)
            if job and job.get("exp"):
                try:
                    job["exp"].close()
                except: pass
                
        task = asyncio.create_task(exp.execute_scan_async(progress, should_stop))
        task.add_done_callback(_done_cb)
            
        _jobs[job_id]["task"] = task
        
        return progress.model_dump()
    
    except Exception as e:
        raise HTTPException(status_code=405, detail=f"执行失败: {str(e)}")

    
class JobInfo(BaseModel):
    job_id: str

@router.post("/status", status_code=200)
def job_status(body: JobInfo):
    
    job_id = body.job_id
    # 1) 先看内存中的运行中任务
    job = _jobs.get(job_id)
    if job:
        return job["progress"].model_dump()

    # 2) 不在内存 → 尝试读文件（已归档）
    path = _job_h5_path(job_id)
    if not os.path.exists(path):
        raise HTTPException(status_code=405, detail="未找到该任务")
    try:
        with h5py.File(path, "r", swmr=True) as f:
            if "metadata/manifest" in f:
                manifest = f["metadata/manifest"][()]
                ret = manifest.decode() if isinstance(manifest, bytes) else manifest
                return json.loads(ret)
            else:
                raise HTTPException(status_code=405, detail=f"读取任务状态失败: 任务未入库")
    except Exception as e:
        raise HTTPException(status_code=405, detail=f"读取任务状态失败: {str(e)}")

@router.post("/log", status_code=200)
def job_log(body: JobInfo):
    
    job_id = body.job_id
    path = _job_log_path(job_id)
    if not os.path.exists(path):
        # 日志文件未生成（log_output=stdout 时仅输出控制台）→ 返回空日志，不报错
        return {"log": ""}
    try:
        with open(path, "r", encoding='utf-8') as f:
            return {
                "log": f.read()
            }
    except Exception as e:
        raise HTTPException(status_code=405, detail=f"读取任务日志失败: {str(e)}")
    

@router.post("/result", status_code=200)
def job_result(body: JobInfo):
    """
    获取实验结果
    数据统一从 HDF5 文件读取，不再缓存到内存
    """
    
    job_id = body.job_id
    path = _job_h5_path(job_id)
    if not os.path.exists(path):
        raise HTTPException(status_code=405, detail="未找到该任务文件")
    try:
        with h5py.File(path, "r", swmr=True) as f:
            
            res = {}
            pd = {}
            if "metadata/manifest" in f:
                manifest = f["metadata/manifest"][()]
                pd = json.loads(manifest.decode() if isinstance(manifest, bytes) else manifest) 
                res['progress'] = pd
            else:
                raise HTTPException(status_code=405, detail="任务文件缺少 metadata/manifest")
            
            done_points = pd['done_points']
            if 'results/count' not in f:
                msg = pd.get("msg") or "结果尚未写入"
                tb = pd.get("traceback")
                detail = f"任务没有 results/count，status={pd.get('status')}, msg={msg}"
                if tb:
                    detail += f"\ntraceback:\n{tb}"
                raise HTTPException(
                    status_code=404,
                    detail=detail,
                )

            #points = expand_points(pd['sweep_list'], mode=pd['expand_mode'])[:done_points]
            #count = f["results/count"][:done_points]
            count = f["results/count"][:done_points].astype(np.float32)
            #result = [
            #    {"axes": label, "result": data.tolist() if isinstance(data, np.ndarray) else data}
            #    for label, data in zip(points, count)
            #]
            #res["data"] = result
            #res["data"] = count if not isinstance(count, np.ndarray) else count.tolist()
            return Response(
                content=count.tobytes(), 
                media_type="application/octet-stream",
                headers={
                    "X-Data-Shape": json.dumps(list(count.shape)),
                    "X-Data-Dtype": str(count.dtype)
                }
            )
            #return res    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=405, detail=f"读取结果失败: {str(e)}")


@router.post("/analysis", status_code=200)
def job_analysis(body: JobInfo):
    """
    获取整次扫描 analysis 结果。
    """

    job_id = body.job_id
    path = _job_h5_path(job_id)
    if not os.path.exists(path):
        raise HTTPException(status_code=405, detail="未找到该任务文件")
    try:
        with h5py.File(path, "r", swmr=True) as f:
            if "results/analysis" not in f:
                raise HTTPException(status_code=404, detail="该任务没有 analysis 结果")
            analysis = f["results/analysis"]
            if isinstance(analysis, h5py.Group):
                data = _read_analysis_group(analysis)
                return Response(
                    content=json.dumps(data, ensure_ascii=False).encode("utf-8"),
                    media_type="application/json",
                )

            data = np.asarray(analysis[()]).astype(np.float32)
            return Response(
                content=data.tobytes(),
                media_type="application/octet-stream",
                headers={
                    "X-Data-Shape": json.dumps(list(data.shape)),
                    "X-Data-Dtype": str(data.dtype)
                }
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=405, detail=f"读取 analysis 失败: {str(e)}")


@router.post("/stop", status_code=200)
def job_stop(body: JobInfo):
    job_id = body.job_id
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=405, detail="该任务已结束或不存在")
    job['cancel']['stop'] = True
    return {"msg": "停止指令已发送"}


@router.post("/fit_method", status_code=200)
def fit_method():
    
    try:
        return {"method": list(asdict(FitMethodSchema()).values())}
    except Exception as e:
        raise HTTPException(status_code=405, detail=f"获取方法失败: {str(e)}")


class FitData(BaseModel):
    x_data: List
    y_data: List
    method: str
    dense: int = 1000
    
@router.post("/fit", status_code=200)
def fit_result(body: FitData):

    try:
        x_dense, y_dense, para = result_fit(body.x_data, body.y_data, body.method, body.dense)
        return {"x_data": x_dense.tolist(), "y_data": y_dense.tolist(), 'params': para}
    except Exception as e:
        raise HTTPException(status_code=405, detail=f"拟合失败: {str(e)}")


class FitPluginData(BaseModel):
    x_data: List
    y_data: List
    fit_script_path: str
    dense: int = 1000


@router.post("/fit_plugin", status_code=200)
def fit_plugin_result(body: FitPluginData):

    try:
        manager = FitPluginManager()
        x_dense, y_dense, params = manager.fit(body.fit_script_path, body.x_data, body.y_data, body.dense)
        return {"x_data": x_dense.tolist(), "y_data": y_dense.tolist(), "params": params}
    except Exception as e:
        raise HTTPException(status_code=405, detail=f"拟合失败: {str(e)}")

class FitSingleShotData(BaseModel):
    iq0: List
    iq1: List
    bins: int = 100
    use_lda: bool = True

@router.post("/fit_single_shot", status_code=200)
def fit_single_shot_result(body: FitSingleShotData):
    try:
        I0=body.iq0[0]
        Q0=body.iq0[1]
        I1=body.iq1[0]
        Q1=body.iq1[1]
        
        data0 = np.column_stack([I0, Q0])
        data1 = np.column_stack([I1, Q1])
        pic_base64 =  analyze_singleshot(data0, data1, body.bins, body.use_lda, plot = False, to_base64 = True)
        return {"img": pic_base64}
    except Exception as e:
        raise HTTPException(status_code=405, detail=f"拟合失败: {str(e)}")