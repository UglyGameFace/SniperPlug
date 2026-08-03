# eBay watcher validation result

- Recorded at: `2026-08-03T01:56:40.165152+00:00`
- Validated commit: `184332faed01617aeadd50bcd5e1fdcc38c19150`

## Status
- Install requirements: **PASS**
- Compile all Python: **PASS**
- Import SniperPlug and eBay watcher runtimes: **PASS**
- Targeted eBay watcher tests: **PASS**
- Complete pytest regression suite: **FAIL (1)**

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

## Compile all Python output

```text

```

## Import SniperPlug and eBay watcher runtimes output

```text

```

## Targeted eBay watcher tests output

```text
..........                                                               [100%]
=============================== warnings summary ===============================
../../../../../opt/hostedtoolcache/Python/3.12.13/x64/lib/python3.12/site-packages/discord/player.py:30
  /opt/hostedtoolcache/Python/3.12.13/x64/lib/python3.12/site-packages/discord/player.py:30: DeprecationWarning: 'audioop' is deprecated and slated for removal in Python 3.13
    import audioop

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
10 passed, 1 warning in 0.33s
```

## Complete pytest regression suite output

```text
tests/test_hp_public_alert_migration.py:75: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 
/opt/hostedtoolcache/Python/3.12.13/x64/lib/python3.12/asyncio/runners.py:195: in run
    return runner.run(main)
           ^^^^^^^^^^^^^^^^
/opt/hostedtoolcache/Python/3.12.13/x64/lib/python3.12/asyncio/runners.py:118: in run
    return self._loop.run_until_complete(task)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/opt/hostedtoolcache/Python/3.12.13/x64/lib/python3.12/asyncio/base_events.py:691: in run_until_complete
    return future.result()
           ^^^^^^^^^^^^^^^
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        db = FakeDatabase(conn)
        await conn.execute(
            """
            CREATE TABLE guild_public_alert_settings (
                guild_id INTEGER PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 0,
                retailers_json TEXT NOT NULL DEFAULT '[]',
                channel_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        await conn.executemany(
            """
            INSERT INTO guild_public_alert_settings
                (guild_id, enabled, retailers_json, channel_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'now', 'now')
            """,
            (
                (1, 1, json.dumps(["walmart"]), "ch:100"),
                (2, 0, json.dumps(["walmart"]), "ch:200"),
                (3, 1, json.dumps(["amazon"]), "ch:300"),
                (4, 1, json.dumps(["walmart", "hp"]), "ch:400"),
            ),
        )
        await conn.commit()
    
        await ensure_public_alert_table(db)
        enabled = await get_public_alert_config(db, 1)
        disabled = await get_public_alert_config(db, 2)
        custom = await get_public_alert_config(db, 3)
        already = await get_public_alert_config(db, 4)
>       assert enabled["retailers"] == ("walmart", "hp")
E       AssertionError: assert ('walmart', 'hp', 'ebay') == ('walmart', 'hp')
E         
E         Left contains one more item: 'ebay'
E         
E         Full diff:
E           (
E               'walmart',
E               'hp',
E         +     'ebay',
E           )

tests/test_hp_public_alert_migration.py:60: AssertionError
=============================== warnings summary ===============================
../../../../../opt/hostedtoolcache/Python/3.12.13/x64/lib/python3.12/site-packages/discord/player.py:30
  /opt/hostedtoolcache/Python/3.12.13/x64/lib/python3.12/site-packages/discord/player.py:30: DeprecationWarning: 'audioop' is deprecated and slated for removal in Python 3.13
    import audioop

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
=========================== short test summary info ============================
FAILED tests/test_hp_public_alert_migration.py::test_existing_enabled_walmart_destinations_receive_hp_once - AssertionError: assert ('walmart', 'hp', 'ebay') == ('walmart', 'hp')
  
  Left contains one more item: 'ebay'
  
  Full diff:
    (
        'walmart',
        'hp',
  +     'ebay',
    )
1 failed, 931 passed, 1 warning in 7.59s
```
