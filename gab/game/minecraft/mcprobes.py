#!/usr/bin/env python3
"""Minecraft regression probes, run INSIDE the sandbox against a running instance (python3 stdlib; called through
`mcgab.py probes`). Port of gamebench g1_regress.MCProbes to the gab bridge (gabbridge: state / cmd / query) + real player
input (xdotool on the instance's display) for movement, building, combat, inventory and containers. Every probe returns a
plain dict (compared with the vanilla run by probes.compare on the host); an exception becomes {"error": ...}.

Order (ORDER): the at-load probes first (player_start, terrain_spawn: before the player moves), then settings, world
behaviour, the real-input probes on a sky pad far from the spawn (PAD, y 200: no terrain, no mobs reach it), save_reload,
death_respawn last (it kills the player). log_errors is read by the host from the game log afterwards.
"""
import json, os, re, subprocess, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import mcgab

ORDER = ["player_start", "terrain_spawn", "difficulty", "game_rules", "time_advances", "weather_changes", "tick_time", "monsters_allowed",
         "move_jump", "place_break", "combat", "inventory_use", "containers", "save_reload", "death_respawn"]
MC_RULES = ["advance_time", "advance_weather", "spawn_mobs", "spawn_monsters", "keep_inventory", "mob_griefing", "natural_health_regeneration",
            "fall_damage", "fire_damage", "drowning_damage", "pvp", "immediate_respawn", "show_death_messages", "random_tick_speed",
            "players_sleeping_percentage", "raids", "spawn_patrols", "block_drops", "respawn_radius"]
PAD = (300, 200, 300)                      # x, floor y + 1 (the player's feet), z


class Probes:
    def __init__(self, inst, port, display):
        self.inst, self.port, self.display = inst, port, display
        self.start = None

    # ---- channels
    def call(self, req, timeout=15):
        r = mcgab.call(self.port, req, timeout)
        if not r.get("ok", True): raise RuntimeError(r.get("error") or "bridge error")
        return r

    def cmd(self, c):
        return self.call({"m": "cmd", "cmd": c})

    def q(self, kind, **args):
        return self.call({"m": "query", "q": kind, "args": args})

    def st(self):
        return mcgab.call(self.port, {"m": "state"}, 5)["engine"]

    def x(self, cmd):
        """xdotool on the instance display, the game window focused first (no window manager: focus is not given)"""
        env = {**os.environ, "DISPLAY": self.display}
        focus = "w=$(xdotool search --onlyvisible --name '^Minecraft' 2>/dev/null | head -1); [ -n \"$w\" ] && xdotool windowfocus $w 2>/dev/null; "
        subprocess.run(["bash", "-c", focus + cmd], env=env, capture_output=True, timeout=60)

    def key(self, k): self.x(f"xdotool key {k}")
    def hold(self, keys, s):
        self.x(" ".join(f"xdotool keydown {k};" for k in keys) + f" sleep {s}; " + " ".join(f"xdotool keyup {k};" for k in keys))
    def mouse(self, button, s=0.0):
        self.x(f"xdotool mousedown {button}; sleep {s}; xdotool mouseup {button}" if s else f"xdotool click {button}")

    def screen_closed(self):
        for _ in range(4):
            if self.st().get("screen") in ("in_game", "dead"): return True
            self.key("Escape"); time.sleep(0.8)
        return self.st().get("screen") == "in_game"

    # ---- the sky pad
    def pad(self, yaw=-90, pitch=0):
        x, y, z = PAD
        self.cmd(f"forceload add {x - 8} {z - 8} {x + 24} {z + 8}"); time.sleep(1.0)
        self.cmd(f"fill {x - 4} {y - 1} {z - 6} {x + 20} {y - 1} {z + 6} stone")
        self.cmd(f"fill {x - 4} {y} {z - 6} {x + 20} {y + 5} {z + 6} air")
        self.cmd(f"kill @e[type=item,x={x},y={y},z={z},distance=..40]")
        self.cmd(f"tp @p {x + 0.5} {y} {z + 0.5} {yaw} {pitch}"); time.sleep(2.0)
        self.screen_closed()

    def face(self, yaw, pitch):
        x, y, z = PAD; self.cmd(f"tp @p {x + 0.5} {y} {z + 0.5} {yaw} {pitch}"); time.sleep(1.0)

    def hotbar9(self, item, n):
        self.cmd(f"item replace entity @p hotbar.8 with {item} {n}"); self.key("9"); time.sleep(0.5)

    # ---- probes
    def player_start(self):
        e = self.st(); sp = self.q("spawn")["pos"]; p = e.get("pos") or [None] * 3
        near = p[0] is not None and abs(p[0] - (sp[0] + 0.5)) <= 12 and abs(p[2] - (sp[2] + 0.5)) <= 12
        self.start = p
        return {"dimension": e.get("dimension"), "near_spawn": near, "spawn": sp, "health": e.get("health"), "food": e.get("food"),
                "game_mode": e.get("game_mode"), "items": sorted((e.get("inventory") or {}).items()), "_pos": p}

    def terrain_spawn(self):
        sp = self.q("spawn")["pos"]
        h = self.q("heightmap", cx=sp[0], cz=sp[2], r=48)
        return {"heights": h["heights"], "center": [sp[0], sp[2]]}

    def difficulty(self): return {"difficulty": self.st().get("difficulty")}

    def game_rules(self):
        r = self.q("gamerules")["rules"]; return {k: r.get(k) for k in MC_RULES}

    def time_advances(self):
        a = self.q("time")["day_time"]; time.sleep(5); b = self.q("time")["day_time"]
        return {"advances": b != a, "_ticks": b - a}

    def weather_changes(self):
        self.cmd("weather rain"); time.sleep(2.5); r = self.q("time")["raining"]
        self.cmd("weather clear"); time.sleep(1.0)
        return {"can_rain": bool(r)}

    def tick_time(self):
        self.pad(); time.sleep(8)
        vals = []
        for _ in range(3): vals.append(self.q("perf")["mspt"]); time.sleep(2)
        return {"mspt": round(sorted(vals)[1], 2), "_all": vals}

    def monsters_allowed(self):
        self.pad(); x, y, z = PAD
        self.cmd(f"summon zombie {x + 6} {y} {z} {{Tags:[\"gabprobe\"],NoAI:1b,PersistenceRequired:1b}}"); time.sleep(3)
        n = len([e for e in self.q("entities", type="minecraft:zombie", x=x, y=y, z=z, r=12)["entities"] if "gabprobe" in (e.get("tags") or [])])
        self.cmd("kill @e[tag=gabprobe]")
        return {"zombie_stays": n > 0}

    def move_jump(self):
        """hold W on the flat pad (walking east), then W + space at a 1-high step 8 blocks ahead (jumping onto it)"""
        self.pad(); x, y, z = PAD
        self.cmd(f"fill {x + 8} {y} {z - 6} {x + 20} {y} {z + 6} stone"); time.sleep(0.5)
        a = self.st()["pos"]; self.hold(["w"], 1.0); time.sleep(0.6); b = self.st()["pos"]
        self.hold(["w", "space"], 2.0); time.sleep(1.0); c = self.st()["pos"]
        return {"walks": b[0] - a[0] >= 2.0, "jumps_onto_step": c[1] >= y + 1, "_dbg": [a, b, c]}

    def place_break(self):
        """dirt in hotbar slot 9, look at the floor 1 block ahead, right click (place), hold left (break)"""
        self.pad(); x, y, z = PAD
        self.hotbar9("dirt", 4); self.face(-90, 60)
        self.mouse(3); time.sleep(1.0)
        placed = self.q("block", x=x + 1, y=y, z=z)["block"] == "minecraft:dirt"
        self.mouse(1, 1.6); time.sleep(1.0)
        broken = self.q("block", x=x + 1, y=y, z=z)["block"] != "minecraft:dirt"
        self.cmd("item replace entity @p hotbar.8 with air"); self.cmd(f"kill @e[type=item,x={x},y={y},z={z},distance=..10]")
        return {"place": placed, "break": broken}

    def combat(self):
        """a NoAI pig 2 blocks ahead, aimed at, one left click"""
        self.pad(); x, y, z = PAD
        self.cmd(f"summon pig {x + 2.5} {y} {z + 0.5} {{Tags:[\"gabprobe\"],NoAI:1b,Rotation:[90f,0f]}}"); time.sleep(1.0)
        self.face(-90, 28); time.sleep(0.5)
        es = [e for e in self.q("entities", type="minecraft:pig", x=x, y=y, z=z, r=6)["entities"] if "gabprobe" in (e.get("tags") or [])]
        if not es: return {"error": "the pig was not summoned"}
        h0 = es[0]["health"]; self.mouse(1); time.sleep(1.0)
        es2 = [e for e in self.q("entities", type="minecraft:pig", x=x, y=y, z=z, r=8)["entities"] if e["id"] == es[0]["id"]]
        h1 = es2[0]["health"] if es2 else 0
        self.cmd("kill @e[tag=gabprobe]"); time.sleep(0.5); self.cmd(f"kill @e[type=item,x={x},y={y},z={z},distance=..10]")
        return {"damage_dealt": h1 < h0, "_h": [h0, h1]}

    def inventory_use(self):
        """E opens the inventory screen; held bread is eaten with the use button (hunger lowered first)"""
        self.pad()
        self.key("e"); time.sleep(1.2); scr = self.st().get("screen"); self.key("Escape"); time.sleep(1.0)
        self.screen_closed()
        self.cmd("effect give @p hunger 8 120"); time.sleep(8.5); self.cmd("effect clear @p")
        f0 = self.st()["food"]
        self.hotbar9("bread", 4); self.face(-90, 0)
        self.mouse(3, 2.5); time.sleep(1.0)
        e = self.st(); bread = (e.get("inventory") or {}).get("minecraft:bread", 0)
        self.cmd("item replace entity @p hotbar.8 with air")
        return {"inventory_screen": scr == "InventoryScreen", "hungry_before": f0 < 20, "ate": bread < 4 and e["food"] > f0,
                "_dbg": {"screen": scr, "food": [f0, e["food"]], "bread": bread}}

    def containers(self):
        """a chest with 3 diamonds 2 blocks ahead: right click opens it, the items are still there after closing"""
        self.pad(); x, y, z = PAD; cx = x + 2
        self.cmd(f"setblock {cx} {y} {z} chest[facing=west]"); self.cmd(f"item replace block {cx} {y} {z} container.0 with diamond 3")
        self.face(-90, 25); time.sleep(0.5)
        self.mouse(3); time.sleep(1.2); scr = self.st().get("screen"); self.key("Escape"); time.sleep(0.8); self.screen_closed()
        kept = self.q("container", x=cx, y=y, z=z)["items"]
        self.cmd(f"setblock {cx} {y} {z} air"); self.cmd(f"kill @e[type=item,x={x},y={y},z={z},distance=..10]")
        return {"opens": scr == "ContainerScreen", "items_kept": kept == {"minecraft:diamond": 3}, "_dbg": {"screen": scr, "chest": kept}}

    def save_reload(self):
        """a gold block on the pad and the player on it; save and quit to the title, reopen the world: both still there"""
        self.pad(); x, y, z = PAD
        self.cmd(f"setblock {x + 3} {y} {z} gold_block"); self.cmd(f"tp @p {x + 0.5} {y} {z + 0.5} -90 0"); time.sleep(1.5)
        world = (self.st().get("world") or "")
        lvl = self.call({"m": "health"})
        self.call({"m": "leave"}, 60)
        t0 = time.time()
        while time.time() - t0 < 60:
            h = mcgab.call(self.port, {"m": "health"}, 5)
            if h.get("phase") == "menu" and not h.get("in_world"): break
            time.sleep(1)
        time.sleep(2)
        wid = json.loads(Path(self.inst, "gab", "start.json").read_text()).get("world", "gab_world")
        self.call({"m": "open", "world": wid}, 60)
        w = mcgab.wait(self.inst, self.port, 120)
        if not w.get("ok"): return {"error": f"the world did not reopen: {w.get('error')}"}
        time.sleep(2)
        self.cmd(f"forceload add {x - 8} {z - 8} {x + 24} {z + 8}"); time.sleep(1)
        blk = self.q("block", x=x + 3, y=y, z=z)["block"]; p = self.st()["pos"]
        self.cmd(f"setblock {x + 3} {y} {z} air")
        return {"block_kept": blk == "minecraft:gold_block", "player_kept": abs(p[0] - (x + 0.5)) < 1.5 and abs(p[2] - (z + 0.5)) < 1.5 and abs(p[1] - y) < 1.5,
                "_dbg": {"block": blk, "pos": p, "world": world, "before": lvl.get("phase")}}

    def death_respawn(self):
        """die away from the spawn with cobblestone in the hotbar: it drops, the player respawns near the world spawn"""
        sp = self.q("spawn")["pos"]
        self.cmd("gamerule immediate_respawn true")
        try:
            self.cmd(f"forceload remove {PAD[0] - 8} {PAD[2] - 8} {PAD[0] + 24} {PAD[2] + 8}")
            self.cmd("spreadplayers 400 400 0 2 false @p"); time.sleep(2)
            self.cmd("item replace entity @p hotbar.8 with cobblestone 3"); time.sleep(0.5)
            self.cmd("kill @p"); time.sleep(5)
            e = self.st(); p = e.get("pos") or [1e9, 0, 1e9]
            near = abs(p[0] - (sp[0] + 0.5)) <= 16 and abs(p[2] - (sp[2] + 0.5)) <= 16
            return {"item_dropped": (e.get("inventory") or {}).get("minecraft:cobblestone", 0) == 0, "respawn_near_spawn": near,
                    "alive": (e.get("health") or 0) > 0, "_dbg": {"pos": p, "spawn": sp, "screen": e.get("screen")}}
        finally:
            self.cmd("gamerule immediate_respawn false"); self.cmd("kill @e[type=item]")


def run(inst, port, display, names, out):
    P = Probes(inst, port, display); res = {}; t0 = time.time()
    order = [n for n in ORDER if n in names]
    for n in order:
        t = time.time()
        try: res[n] = getattr(P, n)()
        except Exception as e: res[n] = {"error": f"{type(e).__name__}: {str(e)[:160]}"}
        res.setdefault("_timing", {})[n] = round(time.time() - t, 1)
        if not mcgab.alive(inst):
            res["_crashed_at"] = n; break
    res["_wall_s"] = round(time.time() - t0, 1)
    Path(out).write_text(json.dumps(res))
    return {"ok": True, "out": out, "n": len(order)}
