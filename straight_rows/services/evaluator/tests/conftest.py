import logging
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from core.config import load_config  # noqa: E402


@pytest.fixture(scope="session")
def cfg():
    c = load_config()
    c["participant"]["github_handle"] = "test-handle"
    c["repository_url"] = "https://github.com/test-handle/straight-rows"
    # the learned site-multipath map belongs to the real field; synthetic farms run without it (tests/test_v2_3.py
    # builds its own)
    c["site_multipath"]["enabled"] = False
    logging.getLogger("sr.alarm").setLevel(logging.WARNING)
    logging.getLogger("sr").setLevel(logging.WARNING)
    return c
