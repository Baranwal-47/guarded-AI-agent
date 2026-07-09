"""Session-wide test env setup.

`db.py` binds its module-level `engine`/`async_session` to
`get_settings().database_url` at *first import*, and `policy_engine.py` now
imports `models` (-> `db`) at module level (02-02: PolicyRule DB rules). That
means any test module importing `gateway`/`policy_engine` at collection time
(test_gateway.py, test_policy_db.py, tests/test_policy_engine.py) triggers
`db`'s first import before test_main.py's own fixture gets a chance to point
DATABASE_URL at its throwaway file — without this, db.py silently binds to
its real default `./guarded_agent.db` and tests write to the actual dev DB.

Setting GEMINI_API_KEY/DATABASE_URL here, in conftest.py, runs before any
test module is collected (pytest always imports conftest.py first), so
whichever module imports `db` first binds to a safe, unique, throwaway file
instead — one fix at the root, not scattered per test file (ponytail).
"""

import os
import tempfile

os.environ.setdefault("GEMINI_API_KEY", "test-key")
os.environ.setdefault(
    "DATABASE_URL",
    f"sqlite+aiosqlite:///{tempfile.mkdtemp(prefix='guarded-ai-test-')}/collection-time.db",
)
