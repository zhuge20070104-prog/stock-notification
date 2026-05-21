"""Pytest config — stub out yfinance for unit tests so we can run on machines
that don't have it installed (e.g. dev hosts). Individual tests can replace the
stub with their own mock via monkeypatch."""
import sys
import types

# Provide a no-op yfinance stub before any business module imports it.
if "yfinance" not in sys.modules:
    stub = types.ModuleType("yfinance")
    def _download(*args, **kwargs):
        import pandas as pd
        return pd.DataFrame()
    class _Ticker:
        def __init__(self, *a, **kw): pass
        @property
        def fast_info(self): return {}
        @property
        def info(self): return {}
        @property
        def news(self): return []
        def history(self, *a, **kw):
            import pandas as pd
            return pd.DataFrame()
    stub.download = _download
    stub.Ticker = _Ticker
    sys.modules["yfinance"] = stub

# boto3 stub (only used in src/store.py, which we don't need for these tests)
if "boto3" not in sys.modules:
    boto3_stub = types.ModuleType("boto3")
    class _Resource:
        def __call__(self, *a, **kw): return self
        def Table(self, *a, **kw): return self
        def scan(self, *a, **kw): return {"Items": []}
        def get_item(self, *a, **kw): return {}
        def put_item(self, *a, **kw): return {}
        def delete_item(self, *a, **kw): return {}
    boto3_stub.resource = lambda *a, **kw: _Resource()
    sys.modules["boto3"] = boto3_stub

# Make `src` importable as a package AND make src/*.py importable as flat modules
# (so backend/api_handler.py which uses `from fetcher import ...` works in tests).
import os
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)
