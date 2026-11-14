"""CONFIDENCE_UPDATE v2.2 §6. Tests 1 and 6 replay the real dev data (skipped when it is not present); 5 is covered
by test_api's batch/live determinism and test_lifecycle's causality truncation, which run with the table loaded."""
import itertools
import json
from pathlib import Path

import pytest
import yaml

from diagnose.confidence import CLASSES, CLIP, confidence, groups_for

HERE = Path(__file__).resolve().parent.parent
ROOT = HERE.parent.parent
TABLE = HERE / "confidence_table.yaml"
DATA = ROOT / "data"
BACKUP = ROOT / "backups" / "v2_1_physics" / "submission.json"
real = pytest.mark.skipif(not (DATA / "labels").is_dir() or not TABLE.is_file(), reason="real data or table missing")


@pytest.fixture(scope="module")
def table():
    if not TABLE.is_file():
        pytest.skip("confidence_table.yaml missing")
    return yaml.safe_load(TABLE.read_text())


def states(cls, which):
    return {g: {"state": which.get(g, "present"), "seen": ["F"]} for g in groups_for(cls)}


# 2 ------------------------------------------------------------------------------------------------------------
def test_v22_2_range(table):
    for c in CLASSES:
        for combo in itertools.product(("present", "absent", "no-info"), repeat=len(groups_for(c))):
            p, _ = confidence(c, states(c, dict(zip(groups_for(c), combo))), table)
            assert CLIP[0] <= p <= CLIP[1]


# 3 ------------------------------------------------------------------------------------------------------------
def test_v22_3_no_info_is_neutral(table):
    for c in CLASSES:
        for g in groups_for(c):
            _, br = confidence(c, states(c, {g: "no-info"}), table)
            assert br["groups"][g]["lr"] == 1.0
            full, _ = confidence(c, states(c, {x: "no-info" for x in groups_for(c)}), table)
            prior = table["detection_prior"]["p"] if table.get("detection_prior") else table["classes"][c]["prior"]
            assert abs(full - round(min(max(prior, CLIP[0]), CLIP[1]), 2)) < 1e-9


# 4 ------------------------------------------------------------------------------------------------------------
def test_v22_4_monotone(table):
    for c in CLASSES:
        p_all, br_all = confidence(c, states(c, {}), table)
        for g in groups_for(c):
            p, br = confidence(c, states(c, {g: "absent"}), table)
            assert br["raw_logodds"] < br_all["raw_logodds"]
            assert p < p_all, (c, g, p, p_all)


# 1 ------------------------------------------------------------------------------------------------------------
@real
@pytest.mark.skipif(not BACKUP.is_file(), reason="v2.1-physics backup missing")
def test_v22_1_detections_unchanged(tmp_path):
    from main import main
    rc = main(["batch", "--data", str(DATA), "--out", str(tmp_path), "--log-level", "ERROR"])
    assert rc == 0
    new = json.loads((tmp_path / "submission.json").read_text())
    old = json.loads(BACKUP.read_text())
    strip = lambda d: [{k: v for k, v in x.items() if k not in ("confidence", "evidence")} for x in d["detections"]]
    assert strip(new) == strip(old)
    assert all(CLIP[0] <= x["confidence"] <= CLIP[1] for x in new["detections"])
    assert all(x["evidence"][-1].startswith("P=") for x in new["detections"])


# 6 ------------------------------------------------------------------------------------------------------------
@real
def test_v22_6_table_deterministic(tmp_path):
    from tools.fit_confidence import main
    a, b = tmp_path / "a.yaml", tmp_path / "b.yaml"
    assert main(["--data", str(DATA), "--table", str(a), "--no-report"]) == 0
    assert main(["--data", str(DATA), "--table", str(b), "--no-report"]) == 0
    assert a.read_bytes() == b.read_bytes()
    assert a.read_bytes() == TABLE.read_bytes()
