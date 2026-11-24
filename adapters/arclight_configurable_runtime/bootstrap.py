"""Local Arclight runtime: no imports from the protected workspace."""
from pathlib import Path
import os
import sys

SOURCE_ROOT = Path(__file__).absolute().parent
ROOT = Path(os.environ.get('QCAL_ARCLIGHT_RUN_DIR', str(SOURCE_ROOT))).absolute()
SERVER = SOURCE_ROOT / 'work' / 'server'
PROTECTED = os.path.normcase(os.path.normpath('D:/qoskit-workspace'))


def _normal(path):
    if isinstance(path, (str, bytes, os.PathLike)):
        return os.path.normcase(os.path.abspath(os.fsdecode(path)))
    return None


def _check(path):
    p = _normal(path)
    if p and (p == PROTECTED or p.startswith(PROTECTED + os.sep)):
        raise PermissionError('Access to the original workspace is prohibited')


def _audit(event, args):
    if event in ('open', 'os.listdir', 'os.scandir', 'os.chdir',
                 'os.remove', 'os.rmdir', 'os.mkdir', 'ctypes.dlopen') and args:
        _check(args[0])
    elif event in ('os.rename', 'os.link', 'os.symlink'):
        _check(args[0]); _check(args[1])


def initialize():
    sys.dont_write_bytecode = True
    sys.addaudithook(_audit)
    for entry in sys.path:
        _check(entry)
    os.chdir(ROOT)
    os.environ['MPLBACKEND'] = 'Agg'
    os.environ['MPLCONFIGDIR'] = str(ROOT / 'runtime_state' / 'matplotlib')
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    sys.path.insert(0, str(SERVER))
    from core.config import Global_Setting
    Global_Setting.set_datapath(str(ROOT / 'results'))
    Global_Setting.set_logpath(str(ROOT / 'logs'))
    Global_Setting.set_setting_path(str(SOURCE_ROOT / 'base_config.json'))
    Global_Setting.set_log_output('file')
    (ROOT / 'results').mkdir(exist_ok=True)
    (ROOT / 'logs').mkdir(exist_ok=True)
    return ROOT


if __name__ == '__main__':
    initialize()
    import core
    print('core:', core.__file__)
    print('python:', sys.executable)
    print('local runtime import passed')
