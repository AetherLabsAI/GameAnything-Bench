"""STATE_SPEC plumbing: state_diff / diff_brief, the replay timeline (state read at every screenshot point), the judge's timeline
text and the play answer's state diff."""
import json
from pathlib import Path

from gab.game import base


def test_state_diff_leaves():
    a = {"running": True, "engine": {"hp": 20, "pos": {"x": 1, "y": 2}}, "mod": {}, "screen_text": ["a", "b"],
         "ents": [{"name": "x", "hp": 3}]}
    b = {"running": True, "engine": {"hp": 15, "pos": {"x": 1}}, "mod": {"vars": {"q": "1"}}, "screen_text": ["b", "c"],
         "ents": [{"name": "x", "hp": 2}, {"name": "y", "hp": 1}]}
    d = base.state_diff(a, b)
    assert d["changed"]["engine.hp"] == [20, 15]
    assert d["changed"]["screen_text"] == {"+": ["c"], "-": ["a"]}
    assert d["changed"]["ents[0].hp"] == [3, 2]
    assert d["added"]["mod.vars.q"] == "1" and d["added"]["ents[1].name"] == "y"
    assert d["removed"]["engine.pos.y"] == 2 and "mod" not in d.get("removed", {})   # filled container: leaves only
    assert base.state_diff(a, json.loads(json.dumps(a))) == {}
    assert base.state_diff(None, {"x": 1}) == {"added": {"x": 1}}
    assert base.state_diff({"l": [1, 2]}, {"l": [2, 1]}) == {"changed": {"l": [[1, 2], [2, 1]]}}   # same items, other order


def test_diff_brief():
    assert base.diff_brief({}) == "no change"
    d = base.state_diff({"e": {"hp": 1}, "t": ["x"]}, {"e": {"hp": 2}, "t": ["x", "hello"], "m": {"k": "v" * 300}})
    s = base.diff_brief(d)
    assert "e.hp: 1 -> 2" in s and 't: +"hello"' in s and "+m.k" in s and len(s) < 300
    big = base.state_diff({}, {f"k{i}": i for i in range(200)})
    s = base.diff_brief(big, limit=200)
    assert len(s) <= 200 and "200 changes in all" in s


class FakeAdapter(base.GameAdapter):
    name = "fake"

    def __init__(self):
        super().__init__(None); self.hp = 20; self.reads = 0; self.inputs = []

    def reset(self, inst): self.hp = 20; return True, "ok"
    def input(self, inst, s): self.inputs.append(s); self.hp -= 1 if s["do"] == "key" else 0
    def screenshot(self, inst, host_path): Path(host_path).write_bytes(b"png"); return str(host_path)
    def state(self, inst):
        self.reads += 1
        return {"running": True, "engine": {"hp": self.hp}, "mod": {}, "screen_text": []}


def test_replay_timeline(tmp_path, monkeypatch):
    from gab import config
    monkeypatch.setattr(config, "get", lambda k, *a: {"replay_auto_shots": 1, "replay_wait_shot_s": 30}.get(k))
    ad = FakeAdapter()
    tr = {"steps": [{"do": "key", "keys": "a"}, {"do": "key", "keys": "b"}, {"do": "shot", "name": "mid"}, {"do": "key", "keys": "c"},
                    {"do": "wait", "seconds": 0}]}
    r = ad.replay("i", tr, tmp_path)
    tl = r["timeline"]
    # start read + auto shot (cap 1) + the agent's shot + end: state read only at those points
    assert ad.reads == 1 + len(tl) and [e["screenshot"] for e in tl][-2:] == ["002_mid.png", "zz_end.png"]
    assert sum(len(e["actions"]) for e in tl) == 4                       # every step appears once, at the next snapshot
    assert tl[-1]["diff"]["changed"]["engine.hp"] == [18, 17]
    j = json.loads((tmp_path / "replay.json").read_text())
    assert j["timeline"] == json.loads(json.dumps(tl)) and (tmp_path / "state" / "start.json").exists()
    assert (tmp_path / j["timeline"][-1]["snapshot"]).exists()


def test_timeline_text_truncates():
    from gab.eval.functions import timeline_text, replay_prompt
    tl = [{"step": i, "screenshot": f"{i:03d}_auto.png", "actions": [{"do": "key", "keys": "w"}],
           "diff": {"changed": {"engine.pos.x": [i, i + 1]}}} for i in range(400)]
    t = timeline_text(tl, limit=3000)
    assert len(t) < 3300 and "000_auto.png" in t and "399_auto.png" in t and "entries left out" in t
    F = [{"id": "FP1", "item": "door opens", "anchors": {"1": "a", "0.5": "b", "0": "c"}}]
    p = replay_prompt("fake", F, [{"trace": "t.yaml", "claims": ["x"], "ok": True, "shots": ["/r/t/000_auto.png"], "timeline": tl[:2], "state": {}}])
    assert "engine.pos.x: 0 -> 1" in p and "state change" in p and "FP1" in p
