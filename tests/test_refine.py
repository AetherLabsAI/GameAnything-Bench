"""refine rounds (gab/refine/refine.py), brief v2 (gab/run/trial.py) and the reference-image path of the image router"""
import json
from pathlib import Path

import pytest

from gab.refine import refine
from gab.run import trial
from gab.host.keyproxy import generations_body


class FakeTask:
    id, game = "T-IO-test", "stardew_valley"
    mdir = Path("/nonexistent")
    def instruction(self, level): return "Make sowing like in the pictures."
    def references(self, kind=None): return []
    def clips(self): return []


def test_brief_has_the_guide_once(tmp_path):
    b = trial.brief_text(FakeTask(), "fine", "stardew_valley", tmp_path, 40)
    assert "How the result is judged" in b and "Do not replace the original game wholesale." in b
    assert b.count('gen-image "PROMPT"') == 1 and "--ref IMG" in b and "gen-sfx" in b
    assert "targets/ has target pictures" not in b                   # no targets staged
    assert b.index("How the result is judged") < b.index("Hand in, in")
    (tmp_path / "targets").mkdir(); (tmp_path / "targets" / "target_01.png").write_bytes(b"x")
    assert "targets/ has target pictures" in trial.brief_text(FakeTask(), "fine", "stardew_valley", tmp_path, 40)


def test_generations_body_passes_reference_images():
    b = generations_body({"prompt": "p", "images": ["data:image/png;base64,AAA", "http://x/y.png"] + ["data:image/png;base64,B"] * 5},
                         "dola-seedream-5.0-lite")
    assert b["image"][0] == "data:image/png;base64,AAA" and len(b["image"]) == 4 and all(i.startswith("data:") for i in b["image"])
    assert "image" not in generations_body({"prompt": "p"}, "m")


def test_copy_targets(tmp_path, monkeypatch):
    monkeypatch.setattr(refine, "targets_dir", lambda: tmp_path / "targets")
    src = tmp_path / "targets" / "T-IO-test"; src.mkdir(parents=True)
    for n in ("target_01.png", "target_02.png", "targets.json"): (src / n).write_text("x")
    w = tmp_path / "work"; w.mkdir()
    assert refine.copy_targets(FakeTask(), w) == 2 and sorted(p.name for p in (w / "targets").iterdir()) == ["target_01.png", "target_02.png"]


def test_refine_is_not_started_without_a_review(tmp_path, monkeypatch):
    d = tmp_path / "trial"; (d / "refine" / "r0" / "critic").mkdir(parents=True)
    (d / "refine" / "r0" / "critic" / "scores.json").write_text(json.dumps({"reference_likeness": 1}))
    started = []
    monkeypatch.setattr(refine, "refine_agent", lambda *a, **k: started.append(1))
    with pytest.raises(RuntimeError, match="no feedback"):
        refine.refine_trial(d, FakeTask(), "fine", "opencode", "m", {}, 1, seat=None)
    assert not started


def test_critic_brief_names_every_kind():
    txt = refine.CRITIC_BRIEF.format(title="G", instruction="i", work="/w", targets="", minutes=10,
                                     kinds=refine.kinds_text())
    assert all(k in txt for k in refine.KINDS) and "scores.json" in txt and "contact sheet" in txt


def test_coverage_brief_and_missing_helpers(tmp_path):
    assert list(refine.KINDS)[0] == "missing_content"
    txt = refine.CRITIC_BRIEF.format(title="G", instruction="i", work="/w", targets="", minutes=15,
                                     kinds=refine.kinds_text())
    assert "Coverage" in txt and '"missing"' in txt
    d = tmp_path / "trial"
    assert refine.missing_parts(d) is None
    assert refine.archive_last_review(d).name == "r0"                       # no round yet: r0 is made for the review
    c = d / "refine" / "r1" / "critic"; c.mkdir(parents=True)
    (c / "scores.json").write_text(json.dumps({"reference_likeness": 2}))
    assert refine.missing_parts(d) is None                                  # old brief: unknown, not skipped
    (c / "scores.json").write_text(json.dumps({"missing": []}))
    assert refine.missing_parts(d) == []
    r = refine.archive_last_review(d)
    assert r.name == "r1" and not (r / "critic").exists() and list(r.glob("critic_old_*"))


def test_rereview_last_is_idempotent(tmp_path):
    d = tmp_path / "trial"; c = d / "refine" / "r1" / "critic"; c.mkdir(parents=True)
    (c / "scores.json").write_text("{}"); (c / "feedback.md").write_text("x")
    (c / "critic.json").write_text(json.dumps({"brief": refine.CRITIC_BRIEF_VERSION}))
    r = refine.archive_last_review(d)                     # already under the current brief: kept, not reviewed again
    assert (r / "critic" / "scores.json").exists() and not list(r.glob("critic_old_*"))
    (c / "critic.json").write_text(json.dumps({"brief": None}))
    r = refine.archive_last_review(d)                     # old brief: archived for a new review
    assert not (r / "critic").exists() and len(list(r.glob("critic_old_*"))) == 1


def test_kind_groups_cover_every_kind_once():
    ks = [k for _, _, g in refine.KIND_GROUPS for k in g]
    assert len(ks) == len(set(ks)) and set(ks) | {"other"} == set(refine.KINDS)
    txt = refine.CRITIC_BRIEF.format(title="G", instruction="i", work="/w", targets="", minutes=15, kinds=refine.kinds_text())
    assert "Part A: request coverage" in txt and "Part B: reference likeness" in txt and "part-B fix" in txt
