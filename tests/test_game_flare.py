"""Flare adapter, offline: deliverable layout / build check, pool ids vs probes, compare on the recorded pool verification
(tests/data/flare: A/A runs aa1 / aa2 and negative mods of 2026-10-30, trimmed: 4 data files, no screenshots)."""
import json, shutil
from pathlib import Path

import yaml

from gab.game import base as gbase
from gab.game.flare import probes as P
from gab.game.flare.adapter import Adapter

D = Path(__file__).parent / "data" / "flare"
REPO = Path(__file__).resolve().parents[1]


def rec(n): return json.loads((D / f"{n}.json").read_text())


def items():
    p = yaml.safe_load((REPO / "benchmark" / "pools" / "flare" / "pool.yaml").read_text())
    return p, [i for i in p["always"] + p["pool"] if i["status"] == "ready"]


def fails(base, got):
    return sorted(i["id"] for i in items()[1] if P.compare_item(i["probe"], base, got)[0] is False)


def test_registered():
    assert "flare" in gbase.GAMES and gbase.title("flare").startswith("Flare")


def test_pool_matches_probes():
    p, its = items()
    names = {i["probe"].split(".")[0] for i in its}
    assert names - {"log_errors"} <= set(P.FLProbes.ALL)
    ids = {i["id"] for i in its}
    assert set(p["default_picks"]) <= ids
    neg = yaml.safe_load((gbase.gdir("flare") / "negmods" / "negmods.yaml").read_text())
    for m, spec in neg.items():
        assert set(spec["expect_fail"]) <= ids and (gbase.gdir("flare") / "negmods" / m / "settings.txt").exists()


def test_aa_and_negatives_on_recorded_runs():
    a = rec("aa1")
    assert fails(a, rec("aa2")) == []
    assert fails(a, rec("neg_slow_hero")) == ["FL-DATA", "FL-MOVE"]
    assert fails(a, rec("neg_tough_zombie")) == ["FL-COMBAT", "FL-DATA", "FL-POWERS"]
    assert "FL-SHOP" in fails(a, rec("neg_dagger_price"))            # its FL-DATA hit lies in a data file the fixture trimmed


def test_compare_edge_cases():
    a = rec("aa1")
    assert P.compare_item("move", a, {"move": {"error": "x"}})[0] is None
    assert P.compare_item("fight", {}, a)[0] is None                       # no baseline -> could not test
    g = json.loads(json.dumps(a["log_errors"])); g["crashed"] = ["town"]
    assert P.compare("log_errors", a["log_errors"], g)[0] is False
    g = json.loads(json.dumps(a["log_errors"])); g["errors"]["ERROR: new kind"] = 1
    assert P.compare("log_errors", a["log_errors"], g)[0] is False


def test_layout_and_build(tmp_path):
    ad = Adapter.__new__(Adapter)
    tpl = gbase.gdir("flare") / "devkit" / "template" / "mod"
    assert [m.name for m in ad._layout(tpl)] == ["gab_template"]
    ok, log = ad.build("x", tpl); assert ok, log
    single = tmp_path / "single"; shutil.copytree(tpl / "gab_template", single)
    assert ad._layout(single) == [single]
    m = tmp_path / "m"; (m / "b").mkdir(parents=True); (m / "a").mkdir()
    for x in ("a", "b"): (m / x / "settings.txt").write_text("game=flare-game\n"); (m / x / "items").mkdir(); (m / x / "items" / "i.txt").write_text("APPEND\n")
    (m / "mods.txt").write_text("b\na\n")
    assert [p.name for p in ad._layout(m)] == ["b", "a"]
    bad = tmp_path / "bad"; (bad / "fantasycore").mkdir(parents=True); (bad / "fantasycore" / "settings.txt").write_text("game=other\n")
    ok, log = ad.build("x", bad)
    assert not ok and "name taken" in log and "game=other" in log and "no data files" in log
    assert not ad.build("x", tmp_path / "empty")[0]


def test_save_helpers():
    s = P.parse_save(P.make_save("maps/perdition_harbor.txt,10,15", [(2, 5), (1, 500)], hpmp=(100, 40)))
    assert P.pos(s) == ("maps/perdition_harbor.txt", 10, 15) and P.items_of(s) == {2: 5, 1: 500} and P.hpmp(s) == (100.0, 40.0)


def test_powers_cast_count_is_noise():
    b = {"mp_costs": [6, 6], "casts": 2, "xp": 4}
    assert P.compare("powers", b, {"mp_costs": [6], "casts": 1, "xp": 4})[0] is True     # zombie died after the first cast
    assert P.compare("powers", b, {"mp_costs": [12, 12], "casts": 2, "xp": 4})[0] is False
    assert P.compare("powers", b, {"mp_costs": [], "casts": 0, "xp": 0})[0] is False


def test_state_compose_and_save_fallback():
    from gab.game.flare import flmon as FM
    from gab.game.flare.adapter import compose_state, engine_from_save
    live = {"running": True, "engine": {"screen": "in game", "map": "maps/arrival.txt", "pos": {"x": 20.5, "y": 20.5}, "extra": 1},
            "mod": {"statuses": ["gab_x"]}, "screen_text": ["hud: hi"]}
    st = compose_state(live)
    assert set(st) == {"running", "engine", "mod", "screen_text"} and list(st["engine"]) == ["source"] + FM.ENGINE_KEYS
    assert st["engine"]["source"] == "memory" and st["engine"]["pos"] == {"x": 20.5, "y": 20.5} and "extra" not in st["engine"]
    assert st["mod"] == {"statuses": ["gab_x"]} and st["screen_text"] == ["hud: hi"]
    sv = P.make_save("maps/perdition_harbor.txt,10,15", [(2, 5), (1, 500)], hpmp=(100, 40), campaign="b,a")
    fb = compose_state({"running": True, "error": "unknown flare binary"}, sv)
    assert fb["engine"]["source"] == "save" and fb["engine"]["map"] == "maps/perdition_harbor.txt" and fb["engine"]["gold"] == 500
    assert fb["engine"]["pos"] == {"x": 10.0, "y": 15.0} and fb["engine"]["statuses"] == ["a", "b"] and fb["engine"]["hp"] == 100
    assert list(fb["engine"]) == list(engine_from_save("")) and fb["error"] == "unknown flare binary"
    off = compose_state({"running": False, "error": "no state socket"})
    assert off["running"] is False and off["engine"]["screen"] is None and "error" not in off
    assert gbase.state_diff(st, compose_state({**live, "engine": {**live["engine"], "pos": {"x": 21.0, "y": 20.5}}})) == \
        {"changed": {"engine.pos.x": [20.5, 21.0]}}


def test_flmon_stock_map_and_status_walk(tmp_path):
    from gab.game.flare import flmon as FM
    stock = tmp_path / "stock"; (stock / "empyrean_campaign" / "maps").mkdir(parents=True); (stock / "fantasycore").mkdir()
    (stock / "empyrean_campaign" / "maps" / "a.txt").write_text(
        "[header]\ntitle=A\n\n[event]\nlocation=1,2,3,4\nset_status=stock_flag\n\n[npc]\nfilename=npcs/x.txt\n\n[enemy]\ncategory=goblin\n")
    user = tmp_path / "user"; (user / "m1" / "maps").mkdir(parents=True); (user / "m1" / "maps" / "a.txt").write_text("APPEND\n")
    (tmp_path / "mods.txt").write_text("fantasycore\nempyrean_campaign\nm1\n")
    d = FM.Data(str(stock), str(user), str(tmp_path / "mods.txt"))
    info = d.stock_map("maps/a.txt")
    assert info["exists"] and info["events"] == [(1, 2, 3, 4)] and info["npcs"] == ["npcs/x.txt"] and "goblin" in info["enemies"]
    assert d.modded("maps/a.txt") == ["m1"] and d.modded("maps/b.txt") == [] and not d.stock_map("maps/b.txt")["exists"]
    assert "stock_flag" in d.stock_statuses(5)

    class FakeMem:                                                     # a 3-node std::map laid out as libstdc++ does
        def __init__(self): self.w = {}
        def put(self, a, v): self.w[a] = v
        def ptr(self, a): return self.w.get(a, 0)
        u64 = ptr
        def u8(self, a): return self.w.get(a, 0)
        def str(self, a, n=200): return self.w.get(a, "")
    m, camp = FakeMem(), 0x1000; mp = camp + FM.O["CampaignManager.status"]; hdr = mp + 8
    A, B, C = 0x2000, 0x3000, 0x4000                                   # B is the root, A left, C right
    for node, par, l, r, flag, name in ((A, B, 0, 0, 1, "a_set"), (B, hdr, A, C, 0, "b_unset"), (C, B, 0, 0, 1, "c_set")):
        m.put(node + 8, par); m.put(node + 16, l); m.put(node + 24, r); m.put(node + 32 + 8, flag); m.put(node + 32 + 16, name)
    m.put(mp + FM.O["StatusMap.header_left"], A); m.put(mp + FM.O["StatusMap.count"], 3)
    assert FM._statuses(m, camp) == ["a_set", "c_set"]


def test_flmon_offsets_match_generated_layout():
    from gab.game.flare import flmon as FM
    j = json.loads((gbase.gdir("flare") / "memlayout" / "offsets.json").read_text())
    alias = {"Map.filename": "MapRenderer.filename", "Map.title": "MapRenderer.title", "Map.events": "MapRenderer.events"}
    for k, v in FM.O.items():
        if alias.get(k, k) in j: assert j[alias.get(k, k)] == v, k


def test_flmon_vecstr_long_vector_is_cut_not_an_error():
    """a HUD log of ~700 messages is real game state: vecstr returns the first / last `cap`, it does not raise
    (2026-11-05: every `game state` failed with 'menus: bad vector' for a mod with many msg= events)"""
    import struct
    from gab.game.flare import flmon as FM
    m = FM.Mem.__new__(FM.Mem)
    base, n = 0x10000, 700
    m.read = lambda a, k: struct.pack("<QQ", base, base + 32 * n) if a == 0x100 else b"\0" * k
    m.str = lambda a, k=200: f"m{(a - base) // 32}"
    assert m.vecstr(0x100, 8, last=True) == [f"m{i}" for i in range(692, 700)]
    assert m.vecstr(0x100, 3) == ["m0", "m1", "m2"]
    m.read = lambda a, k: struct.pack("<QQ", base, base + 32 * 10 ** 7) if a == 0x100 else b"\0" * k
    import pytest
    with pytest.raises(ValueError): m.vecstr(0x100, 8)                # a corrupt size still fails the sanity limit
