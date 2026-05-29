from __future__ import annotations

import asyncio
import importlib
import inspect
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def pytest_configure(config):
    config.addinivalue_line("markers", "asyncio: run async test functions with a local event loop")


def pytest_runtest_setup(item):
    """Keep focused Walmart price-proof tests isolated from full-suite module state.

    The full suite imports and exercises Walmart provider helpers through several
    paths before test_walmart_price_proof runs. Refresh the module alias for that
    file so it tests the actual provider implementation from disk, not stale
    state left over by earlier imports.
    """
    module = getattr(item, "module", None)
    if module is None or getattr(module, "__name__", "") != "test_walmart_price_proof":
        return None

    import sniperplug.providers.walmart as walmart

    walmart = importlib.reload(walmart)
    for name in (
        "_best_reference_context_price",
        "_trusted_reference_price",
        "_walmart_promotion_proof",
    ):
        if hasattr(module, name):
            setattr(module, name, getattr(walmart, name))
    return None


def pytest_pyfunc_call(pyfuncitem):
    testfunction = pyfuncitem.obj
    if not inspect.iscoroutinefunction(testfunction):
        return None
    if pyfuncitem.get_closest_marker("asyncio") is None:
        return None
    fixture_names = pyfuncitem._fixtureinfo.argnames
    kwargs = {name: pyfuncitem.funcargs[name] for name in fixture_names}
    asyncio.run(testfunction(**kwargs))
    return True
