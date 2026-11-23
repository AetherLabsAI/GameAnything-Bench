"""VoxeLibre adapter without a sandbox: the STATE_SPEC shaping of the bridge answer, the state self-check files."""
import yaml

from gab.game import base as gbase
from gab.game.voxelibre import adapter as A


def test_shape_state_fixed_keys():
    st = A.shape_state({"running": True, "engine": {"hp": 20, "pos": {"x": 1.5, "y": 2, "z": 3}, "inventory": None, "extra": 1},
                        "mod": {"mods": None, "entities": [], "storage": {"m": {"source": "db", "values": None}}}, "screen_text": ["hud: hi"]})
    assert st["running"] is True and list(st["engine"]) == A.ENGINE_KEYS and st["engine"]["hp"] == 20 and st["engine"]["food"] is None
    assert st["engine"]["inventory"] == {} and st["engine"]["objects_near"] == {} and "extra" not in st["engine"]
    assert st["mod"] == {"mods": [], "entities": {}, "player_meta": {}, "storage": {"m": {"source": "db", "values": {}}}, "node_meta": {}}
    assert st["screen_text"] == ["hud: hi"]


def test_shape_state_errors_and_old_bridge():
    st = A.shape_state({"running": False})
    assert st["running"] is False and st["mod"]["entities"] == {} and st["screen_text"] == [] and all(v is None for k, v in st["engine"].items() if k not in ("inventory", "objects_near"))
    st = A.shape_state({"running": True, "error": "lua: x" * 200, "hooks": False})
    assert len(st["error"]) == 300 and "old bridge" in st["note"]
    assert A.shape_state(None)["running"] is False


def test_vanilla_meta_lua():
    assert A.VANILLA_META_LUA.startswith("if not V.gab_vanilla_meta then") and '["gamemode"] = true' in A.VANILLA_META_LUA


def test_statecheck_files():
    d = gbase.gdir("voxelibre") / "statecheck"
    spec = yaml.safe_load((d / "statecheck.yaml").read_text())
    assert (d / spec["mod"]["dir"] / "gabtest" / "init.lua").exists()
    assert {e.split(":")[0] for e in spec["mod"]["expect"]} == {"mod", "screen_text"}
    assert set(spec["vanilla"]["engine"]) <= {f"pos.{c}" for c in "xyz"} | set(A.ENGINE_KEYS)
