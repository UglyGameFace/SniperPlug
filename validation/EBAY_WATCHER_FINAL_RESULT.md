# Final eBay watcher validation result

- Recorded at: `2026-08-03T02:42:24.166272+00:00`
- Validated commit: `dfb4da8a797e0b9060284115a6161faa03812dc5`

## Status
- Install requirements: **PASS**
- Git diff whitespace validation: **PASS**
- No legacy split-budget references: **PASS**
- Compile all Python: **PASS**
- Import SniperPlug and eBay watcher runtimes: **PASS**
- Targeted eBay watcher tests: **FAIL (4)**
- Complete pytest regression suite: **FAIL (2)**

## Install requirements output

```text
Requirement already satisfied: pip in /opt/hostedtoolcache/Python/3.12.13/x64/lib/python3.12/site-packages (26.1.2)
Collecting pip
  Using cached pip-26.2-py3-none-any.whl.metadata (4.6 kB)
Using cached pip-26.2-py3-none-any.whl (1.8 MB)
Installing collected packages: pip
  Attempting uninstall: pip
    Found existing installation: pip 26.1.2
    Uninstalling pip-26.1.2:
      Successfully uninstalled pip-26.1.2
Successfully installed pip-26.2
Collecting discord.py (from -r requirements.txt (line 1))
  Using cached discord_py-2.7.1-py3-none-any.whl.metadata (7.5 kB)
Collecting aiohttp<4,>=3.9 (from -r requirements.txt (line 2))
  Using cached aiohttp-3.14.3-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl.metadata (8.3 kB)
Collecting aiosqlite (from -r requirements.txt (line 3))
  Using cached aiosqlite-0.22.1-py3-none-any.whl.metadata (4.3 kB)
Collecting libsql (from -r requirements.txt (line 9))
  Using cached libsql-0.1.11-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl.metadata (281 bytes)
Collecting python-dotenv (from -r requirements.txt (line 10))
  Using cached python_dotenv-1.2.2-py3-none-any.whl.metadata (27 kB)
Collecting cryptography (from -r requirements.txt (line 11))
  Using cached cryptography-50.0.0-cp311-abi3-manylinux_2_34_x86_64.whl.metadata (4.3 kB)
Collecting pytest (from -r requirements.txt (line 12))
  Using cached pytest-9.1.1-py3-none-any.whl.metadata (7.6 kB)
Collecting aiohappyeyeballs>=2.5.0 (from aiohttp<4,>=3.9->-r requirements.txt (line 2))
  Using cached aiohappyeyeballs-2.7.1-py3-none-any.whl.metadata (5.9 kB)
Collecting aiosignal>=1.4.0 (from aiohttp<4,>=3.9->-r requirements.txt (line 2))
  Using cached aiosignal-1.4.0-py3-none-any.whl.metadata (3.7 kB)
Collecting attrs>=17.3.0 (from aiohttp<4,>=3.9->-r requirements.txt (line 2))
  Using cached attrs-26.1.0-py3-none-any.whl.metadata (8.8 kB)
Collecting frozenlist>=1.1.1 (from aiohttp<4,>=3.9->-r requirements.txt (line 2))
  Using cached frozenlist-1.8.0-cp312-cp312-manylinux1_x86_64.manylinux_2_28_x86_64.manylinux_2_5_x86_64.whl.metadata (20 kB)
Collecting multidict<7.0,>=4.5 (from aiohttp<4,>=3.9->-r requirements.txt (line 2))
  Using cached multidict-6.7.1-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl.metadata (5.3 kB)
Collecting propcache>=0.2.0 (from aiohttp<4,>=3.9->-r requirements.txt (line 2))
  Using cached propcache-0.5.2-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl.metadata (16 kB)
Collecting typing_extensions>=4.4 (from aiohttp<4,>=3.9->-r requirements.txt (line 2))
  Using cached typing_extensions-4.16.0-py3-none-any.whl.metadata (3.3 kB)
Collecting yarl<2.0,>=1.17.0 (from aiohttp<4,>=3.9->-r requirements.txt (line 2))
  Using cached yarl-1.24.5-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl.metadata (103 kB)
Collecting idna>=2.0 (from yarl<2.0,>=1.17.0->aiohttp<4,>=3.9->-r requirements.txt (line 2))
  Using cached idna-3.18-py3-none-any.whl.metadata (6.1 kB)
Collecting cffi>=2.0.0 (from cryptography->-r requirements.txt (line 11))
  Using cached cffi-2.1.0-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.whl.metadata (2.5 kB)
Collecting iniconfig>=1.0.1 (from pytest->-r requirements.txt (line 12))
  Using cached iniconfig-2.3.0-py3-none-any.whl.metadata (2.5 kB)
Collecting packaging>=22 (from pytest->-r requirements.txt (line 12))
  Using cached packaging-26.2-py3-none-any.whl.metadata (3.5 kB)
Collecting pluggy<2,>=1.5 (from pytest->-r requirements.txt (line 12))
  Using cached pluggy-1.6.0-py3-none-any.whl.metadata (4.8 kB)
Collecting pygments>=2.7.2 (from pytest->-r requirements.txt (line 12))
  Using cached pygments-2.20.0-py3-none-any.whl.metadata (2.5 kB)
Collecting pycparser (from cffi>=2.0.0->cryptography->-r requirements.txt (line 11))
  Using cached pycparser-3.0-py3-none-any.whl.metadata (8.2 kB)
Using cached aiohttp-3.14.3-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl (1.8 MB)
Using cached multidict-6.7.1-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl (256 kB)
Using cached yarl-1.24.5-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl (109 kB)
Using cached discord_py-2.7.1-py3-none-any.whl (1.2 MB)
Using cached aiosqlite-0.22.1-py3-none-any.whl (17 kB)
Using cached libsql-0.1.11-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl (5.1 MB)
Using cached python_dotenv-1.2.2-py3-none-any.whl (22 kB)
Using cached cryptography-50.0.0-cp311-abi3-manylinux_2_34_x86_64.whl (4.7 MB)
Using cached pytest-9.1.1-py3-none-any.whl (386 kB)
Using cached pluggy-1.6.0-py3-none-any.whl (20 kB)
Using cached aiohappyeyeballs-2.7.1-py3-none-any.whl (15 kB)
Using cached aiosignal-1.4.0-py3-none-any.whl (7.5 kB)
Using cached attrs-26.1.0-py3-none-any.whl (67 kB)
Using cached cffi-2.1.0-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.whl (221 kB)
Using cached frozenlist-1.8.0-cp312-cp312-manylinux1_x86_64.manylinux_2_28_x86_64.manylinux_2_5_x86_64.whl (242 kB)
Using cached idna-3.18-py3-none-any.whl (65 kB)
Using cached iniconfig-2.3.0-py3-none-any.whl (7.5 kB)
Using cached packaging-26.2-py3-none-any.whl (100 kB)
Using cached propcache-0.5.2-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl (61 kB)
Using cached pygments-2.20.0-py3-none-any.whl (1.2 MB)
Using cached typing_extensions-4.16.0-py3-none-any.whl (45 kB)
Using cached pycparser-3.0-py3-none-any.whl (48 kB)
Installing collected packages: typing_extensions, python-dotenv, pygments, pycparser, propcache, pluggy, packaging, multidict, libsql, iniconfig, idna, frozenlist, attrs, aiosqlite, aiohappyeyeballs, yarl, pytest, cffi, aiosignal, cryptography, aiohttp, discord.py

Successfully installed aiohappyeyeballs-2.7.1 aiohttp-3.14.3 aiosignal-1.4.0 aiosqlite-0.22.1 attrs-26.1.0 cffi-2.1.0 cryptography-50.0.0 discord.py-2.7.1 frozenlist-1.8.0 idna-3.18 iniconfig-2.3.0 libsql-0.1.11 multidict-6.7.1 packaging-26.2 pluggy-1.6.0 propcache-0.5.2 pycparser-3.0 pygments-2.20.0 pytest-9.1.1 python-dotenv-1.2.2 typing_extensions-4.16.0 yarl-1.24.5
```

## Git diff whitespace validation output

```text

```

## No legacy split-budget references output

```text

```

## Compile all Python output

```text

```

## Import SniperPlug and eBay watcher runtimes output

```text

```

## Targeted eBay watcher tests output

```text
ERROR: file or directory not found: tests/test_ebay_watcher_exact_identity.py


no tests ran in 0.00s
```

## Complete pytest regression suite output

```text

==================================== ERRORS ====================================
__________ ERROR collecting tests/test_ebay_watcher_shared_budget.py ___________
ImportError while importing test module '/home/runner/work/SniperPlug/SniperPlug/tests/test_ebay_watcher_shared_budget.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
/opt/hostedtoolcache/Python/3.12.13/x64/lib/python3.12/importlib/__init__.py:90: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
tests/test_ebay_watcher_shared_budget.py:9: in <module>
    from sniperplug.ebay_watcher.models import EbayAPIBudgetExceeded
E   ImportError: cannot import name 'EbayAPIBudgetExceeded' from 'sniperplug.ebay_watcher.models' (/home/runner/work/SniperPlug/SniperPlug/sniperplug/ebay_watcher/models.py)
=============================== warnings summary ===============================
../../../../../opt/hostedtoolcache/Python/3.12.13/x64/lib/python3.12/site-packages/discord/player.py:30
  /opt/hostedtoolcache/Python/3.12.13/x64/lib/python3.12/site-packages/discord/player.py:30: DeprecationWarning: 'audioop' is deprecated and slated for removal in Python 3.13
    import audioop

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
=========================== short test summary info ============================
ERROR tests/test_ebay_watcher_shared_budget.py
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
1 warning, 1 error in 2.66s
```
