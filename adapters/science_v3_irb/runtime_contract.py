"""Existing approved worker environment; no installation and no API dependency."""
import importlib
import sys

EXPECTED={'python':'3.12.10','numpy':'2.5.2','scipy':'1.18.1'}

def read_versions():
    return {'python':'.'.join(map(str,sys.version_info[:3])),
            'numpy':importlib.import_module('numpy').__version__,
            'scipy':importlib.import_module('scipy').__version__}

def check_runtime():
    actual=read_versions()
    if actual!=EXPECTED:raise ValueError('Native numeric runtime differs from verified existing Python312 environment: '+str(actual))
    return {'policy':'existing_native_numeric_runtime/1','expected':dict(EXPECTED),'actual':actual,
            'pythonExecutable':sys.executable,'installedByThisChange':False}
