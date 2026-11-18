import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from datetime import datetime
from ..config import Global_Setting
import sys

def make_exp_file_handler(exp_id: str, log_dir: str):
    """
    日志处理器
    """
    
    if Global_Setting.get_log_output() == 'file':
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        log_path = Path(log_dir) / f"{exp_id}.log"
        h = RotatingFileHandler(
            log_path,
            maxBytes=20 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
    else:
        h = logging.StreamHandler(sys.stdout)
    
    fmt = logging.Formatter("[%(asctime)s]: %(message)s")
    h.setFormatter(fmt)
    level = logging._nameToLevel[Global_Setting.get_log_level().upper()]
    h.setLevel(level)
    return h

def attach_handler_to_devices(handler, devices):
    """
    将日志头绑定到设备的日志中
    """
    snapshot = []
    for dev in devices:
        logger = dev if isinstance(dev, logging.Logger) else dev.log

        # 避免重复挂载（同一次实验里重复 add）
        if handler in logger.handlers:
            continue
        
        snapshot.append((logger, logger.propagate))
        
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)

        # 关键：防止向上冒泡导致写入其它地方/重复写
        logger.propagate = False

    return snapshot

def detach_handler_from_devices(handler, devices):
    
    for dev in devices:
        logger = dev if isinstance(dev, logging.Logger) else dev.log

        if handler not in logger.handlers:
            continue

        logger.removeHandler(handler)


def create_experiment_logger(exp_id: str) -> logging.Logger:
    """
    生成实验对应的日志文件
    """
    
    log_dir = Path(Global_Setting.get_logpath())
    
    handler = make_exp_file_handler(exp_id, log_dir=log_dir)

    exp_logger = logging.getLogger(f"{exp_id}")
    exp_logger.setLevel(logging.INFO)
    exp_logger.addHandler(handler)
    exp_logger.propagate = False

    return handler, exp_logger

def close_experiment_logger(exp_log):
    
    for h in list(exp_log.handlers):
        try:
            h.flush()
            h.close()
        finally:
            exp_log.removeHandler(h)