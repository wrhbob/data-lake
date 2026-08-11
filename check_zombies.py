import os, sys
from pathlib import Path
from dotenv import load_dotenv
load_dotenv('.env', override=False)
sys.path.insert(0, 'file_asset_service')
from app.database import get_engine
from sqlalchemy import text

with get_engine().connect() as c:
    my_ip = c.execute(text('SELECT inet_client_addr()')).scalar()
    rows = c.execute(text('''
        SELECT pid, state, now() - xact_start AS xact_age, substring(query, 1, 80) AS q
        FROM pg_stat_activity
        WHERE datname = current_database()
          AND pid <> pg_backend_pid()
          AND COALESCE(client_addr, inet('127.0.0.1')) = COALESCE(:my_ip, inet('127.0.0.1'))
          AND (state IN ('idle in transaction', 'idle in transaction (aborted)')
            OR (state = 'active' AND wait_event_type = 'Lock' AND now() - state_change > interval '1 min'))
        ORDER BY xact_start NULLS LAST
    '''), {'my_ip': my_ip}).all()
    if not rows:
        print(f'my ip={my_ip}, no zombie PG connections, ok')
    else:
        print(f'my ip={my_ip}, stale PG connections {len(rows)}:')
        for r in rows:
            print(f'  pid={r.pid} state={r.state} xact_age={r.xact_age} q={r.q}')
        for r in rows:
            ok = c.execute(text('SELECT pg_terminate_backend(:p)'), {'p': r.pid}).scalar()
            print(f'  pg_terminate_backend({r.pid}) -> {ok}')