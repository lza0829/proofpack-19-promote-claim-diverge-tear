import os
import tempfile

import pytest

# 必须在导入 app 之前固定数据目录（config/db 在 import 时读取）
_TMP = tempfile.mkdtemp(prefix="proofpack-test-")
os.environ["DATA_DIR"] = _TMP

from app.db import DB_PATH  # noqa: E402
from app.seed import init_db  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    if DB_PATH.exists():
        DB_PATH.unlink()
    init_db()
    yield
