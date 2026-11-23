"""Colobot: Gold Edition regression probes. Ported from the original probes_colobot.py (verified in game
2026-10-30, Colobot: Gold Edition 0.2.1-alpha = Ubuntu noble colobot 0.2.1-1build3; A/A aa9 vs aa10 + negative mods in
pool_colobot.yaml). Changes: runs through a gab Seat (any host backend) instead of docker exec on a fixed container; install
root / display are arguments; files go in as one tar through seat.put; the runner is launched with `setsid -f` (the exec
returns at once) instead of a cut 4 s docker exec; rejected exec streams are retried (remote docker 503 too); the gaprobe mod and the runner are written per probe instance (seats
of one sandbox share the install; the display is baked into each runner); an instance's run dirs are deleted after its probes; no CLI. Probe levels,
CBOT programs, key scripts, compare() rules and tolerances are unchanged.

Bridge: Colobot's own level files + CBOT. Every probe is one short game run started with `-runscene challenges9NN`: the probe
levels live in a data mod "gaprobe" (levels/challenges/chapter009/levelNNN/scene.txt + ai/cbp_*.txt). Bots in the probe level
run CBOT programs that measure with the game's own API and write `key=value` lines to <savedir>/files/<probe>_<tag>.txt (CBOT
`file` class), ending with END. Keyboard probes (astronaut, quicksave) press real keys with xdotool on the game window (one
keyboard run per sandbox at a time: flock on <root>/keys.lock). Mods under test = Colobot data mods (a directory overlaying the
data dir): copied to <savedir>/mods/<name> and enabled in <savedir>/colobot.ini after gaprobe.
Rendering: Mesa llvmpipe on the seat's display, NOT vglrun (on the shared H100 the GPU path ran at a few fps and Colobot's
physics broke); 800x600, shadows off, sound off.
"""
import io, json, os, re, shlex, tarfile, time
from pathlib import Path

VERSION = "Colobot: Gold Edition 0.2.1-alpha (Ubuntu noble colobot 0.2.1-1build3)"

# ------------------------------------------------------------------ in-pod runner ---------------------------------------------------------
RUN_SH = r'''#!/bin/bash
# run_cb.sh SAVEDIR SCENE TIMEOUT_S TAGS KEYS : one probe run. SAVEDIR is prepared by the host (colobot.ini, mods/). Ends when
# every tag in TAGS (comma list) has END in files/<tag>.txt, or at TIMEOUT_S. KEYS: optional key script (xdotool) run when
# files/keys.go appears (one "key|keydown|keyup|sleep|shot ARG" per line). Writes SAVEDIR/exit.json.
D=__ROOT__; S=$1; SC=$2; TO=$3; TAGS=$4; KEYS=$5
echo $$ > $S/launched     # the host checks this (a keyboard run may wait minutes for the lock before the game starts)
export LD_LIBRARY_PATH=$D/root/usr/lib/x86_64-linux-gnu:$D/root/usr/lib/colobot DISPLAY=__DISPLAY__ SDL_AUDIODRIVER=dummy
# keyboard runs hold the seat's keyboard lock for the whole run (focus goes to one game window at a time)
# and no new game window may appear meanwhile (SDL takes the focus when its window maps): other runs wait for it to end
if [ -n "$KEYS" ]; then exec 8>$D/keys.lock; flock 8; else flock -s $D/keys.lock true; fi
T0=$(date +%s%N); EXTRA=""; [ -n "$CB_LOADSAVE" ] && EXTRA="-loadsave $CB_LOADSAVE"
if [ -n "$CB_LOADSAVE" ]; then setsid $D/root/usr/games/colobot -datadir $D/root/usr/share/games/colobot -langdir $D/root/usr/share/locale -savedir $S -loadsave "$CB_LOADSAVE" -resolution 800x600 > $S/game.log 2>&1 < /dev/null &
else setsid $D/root/usr/games/colobot -datadir $D/root/usr/share/games/colobot -langdir $D/root/usr/share/locale -savedir $S -runscene $SC -resolution 800x600 > $S/game.log 2>&1 < /dev/null & fi
P=$!; echo $P > $S/pid; DONE=false; KEYED=false
while kill -0 $P 2>/dev/null; do
  N=$(( ($(date +%s%N) - T0) / 1000000000 )); [ $N -ge $TO ] && break
  ALL=true; for t in ${TAGS//,/ }; do grep -qx END $S/files/$t.txt 2>/dev/null || { ALL=false; break; }; done
  $ALL && { DONE=true; break; }
  if [ -n "$KEYS" ] && ! $KEYED && [ -e $S/files/keys.go ]; then
    KEYED=true
    W=$(xdotool search --pid $P 2>/dev/null | tail -1)
    ( xdotool windowactivate --sync $W 2>/dev/null; xdotool windowfocus --sync $W 2>/dev/null; sleep 0.3
      while read -r c a; do case $c in
        key|keydown|keyup) xdotool windowfocus $W 2>/dev/null; xdotool $c $a ;; sleep) sleep $a ;;
        shot) ffmpeg -loglevel error -y -f x11grab -video_size 1280x720 -i $DISPLAY -frames:v 1 $S/$a.png ;; esac
      done < $KEYS; echo done > $S/files/keys.done ) &
  fi
  sleep 0.5
done
ALIVE=false; kill -0 $P 2>/dev/null && ALIVE=true
[ -n "$CB_SHOT" ] && $ALIVE && ffmpeg -loglevel error -y -f x11grab -video_size 1280x720 -i $DISPLAY -frames:v 1 $S/end.png
if $ALIVE; then kill -TERM -$P 2>/dev/null; for i in 1 2 3 4 5 6; do kill -0 $P 2>/dev/null || break; sleep 0.5; done; kill -KILL -$P 2>/dev/null; fi
wait $P 2>/dev/null; RC=$?
echo "{\"done\": $DONE, \"alive_at_end\": $ALIVE, \"rc\": $RC, \"wall_s\": $(( ($(date +%s%N) - T0) / 1000000 ))e-3}" > $S/exit.json
'''

INI = """[Mods]
Names={names}
Enabled={enabled}
[Setup]
FocusLostPause=0
FocusLostMute=1
Autosave=0
Movies=0
Tooltips=0
ShadowMapping=0
MSAA=1
AudioVolume=0
MusicVolume=0
Resolution=800x600
Fullscreen=0
[Language]
Lang=en
[Gamer]
LastName=Player
"""

# ------------------------------------------------------------------ probe levels ----------------------------------------------------------
HEAD = """Title.E text="GameAnything probe {name}"
Resume.E text="GameAnything regression probe level ({name})"
EndingFile win=-1 lost=-1
#Include file="levels/missions/chapter001/planet.txt"
TerrainRelief image="relief11.png" factor=1.0
TerrainResource image="res05.png"
#Include file="levels/missions/chapter001/terrain.txt"
TerrainCreate
MaxFlyingHeight max=40
{research}
BeginObject
CreateLight dir= 0.0;-1.0; 0.0 color= 0.63; 0.63; 0.63 type=Terrain
CreateLight dir= 1.0; 0.0;-1.0 color=-0.70;-0.70;-0.70 type=Terrain
CreateLight dir=-1.0; 0.0; 1.0 color= 1.40; 1.40; 1.40 type=Terrain
CreateLight dir=-1.0;-1.0; 1.0 color= 0.56; 0.56; 0.56 type=Object
CreateLight dir= 1.0;-1.0; 1.0 color= 0.32; 0.32; 0.32 type=Object
CreateLight dir=-1.0;-1.0;-1.0 color= 0.32; 0.32; 0.32 type=Object
CreateLight dir= 1.0;-1.0;-1.0 color= 0.16; 0.16; 0.16 type=Object
{objects}
{ending}
"""
ALL_RESEARCH = "\n".join(f"EnableResearch type={r}\nDoneResearch type={r}" for r in
                         ["TRACKER", "WINGER", "THUMPER", "SHOOTER", "TOWER", "PHAZER", "SHIELDER", "ATOMIC", "iPAW", "iGUN", "RECYCLER", "SUBBER", "SNIFFER", "BUILDER", "TARGET"]) + "\n" + \
    "\n".join(f"EnableBuild type={b}" for b in ["BotFactory", "Derrick", "Converter", "RadarStation", "PowerPlant", "NuclearPlant",
                                                 "PowerStation", "RepairCenter", "DefenseTower", "ResearchCenter", "AutoLab", "PowerCaptor",
                                                 "ExchangePost", "FlatGround", "Flag"])


def cbot(tag, body, fn=None):
    """A probe program: `body` uses W("k=" + v) to record; the file ends with END (the runner waits for it)."""
    fn = fn or tag
    return f'''extern void object::{tag.title().replace("_", "")}()
{{
	errmode(0);
	ipf(100);
{body}
	W("END");
}}
void W(string s)
{{
	file f();
	f.open("{fn}.txt", "a");
	f.writeln(s);
	f.close();
}}
'''


# common CBOT snippets
P2 = 'round(position.x*100)/100 + ";" + round(position.y*100)/100 + ";" + round(position.z*100)/100'

MOTION_CHASSIS = {"wheeled": ("WheeledGrabber", -10), "tracked": ("TrackedGrabber", 5), "legged": ("LeggedGrabber", 20), "winged": ("WingedGrabber", 35)}
MOTION_BODY = '''	wait(1);
	float t; float e0 = energyCell.energyLevel; point p0 = position; int e;
	W("start=" + P2 + ";" + orientation);
	t = abstime(); e = move(10);
	W("move10_err=" + e); W("move10_s=" + (abstime()-t)); W("move10_dist=" + distance2d(p0, position));
	t = abstime(); e = turn(90);
	W("turn90_err=" + e); W("turn90_s=" + (abstime()-t)); W("turn_orient=" + orientation);
	p0 = position; t = abstime(); e = move(-5);
	W("back5_err=" + e); W("back5_s=" + (abstime()-t)); W("back5_dist=" + distance2d(p0, position));
	point g; g.x = position.x + 12; g.y = position.y; g.z = 0;
	p0 = position; t = abstime(); e = goto(g);
	W("goto_err=" + e); W("goto_s=" + (abstime()-t)); W("goto_miss=" + distance2d(position, g));
	W("energy_used=" + (e0 - energyCell.energyLevel));
	W("end=" + P2);
'''.replace("P2", P2)

LANG_BODY = r'''
	int i; int s = 0; float x = 0;
	for (i = 1; i <= 100; i++) { s += i*i % 7; x += sqrt(i) / 3; }
	W("loop_int=" + s); W("loop_float=" + round(x*10000)/10000);
	W("math=" + round(sin(30)*1000)/1000 + ";" + round(cos(60)*1000)/1000 + ";" + round(atan(1)*1000)/1000 + ";" + pow(2, 10) + ";" + floor(-2.5) + ";" + ceil(2.1) + ";" + abs(-3) + ";" + (17 / 5) + ";" + (17 % 5) + ";" + (17.0 / 4));
	string a = "Colobot Gold";
	W("str=" + strlen(a) + ";" + strleft(a, 3) + ";" + strright(a, 4) + ";" + strmid(a, 2, 3) + ";" + strfind(a, "Gold") + ";" + strupper(a) + ";" + strlower(a) + ";" + strval("42.5"));
	int arr[]; for (i = 0; i < 10; i++) arr[i] = (i * 37) % 11;
	string sa = ""; for (i = 0; i < sizeof(arr); i++) sa += arr[i] + ",";
	W("array=" + sa + "n" + sizeof(arr));
	W("fact=" + Fact(10) + ";" + Fib(15));
	CbpAcc acc(); for (i = 0; i < 5; i++) acc.Add(i * 2.5);
	W("class=" + acc.total + ";" + acc.n + ";" + acc.Mean());
	string sw = ""; for (i = 0; i < 4; i++) { switch (i) { case 0: sw += "a"; break; case 1: sw += "b"; case 2: sw += "c"; break; default: sw += "d"; } }
	W("switch=" + sw);
	int w = 0; do { w++; if (w == 3) continue; if (w > 6) break; } while (true);
	W("dowhile=" + w);
	bool bb = (3 > 2) and not (1 == 2) or false; W("bool=" + bb + ";" + (5 & 3) + ";" + (5 | 3) + ";" + (5 ^ 3) + ";" + (1 << 4) + ";" + (-16 >> 2));
	W("cat=" + WheeledGrabber + ";" + Titanium + ";" + AlienAnt + ";" + Me + ";" + PowerCell + ";" + BotFactory);
	W("self=" + category + ";" + round(energyCell.energyLevel*100)/100 + ";" + round(shieldLevel*100)/100 + ";" + round(orientation));
	W("time=" + (abstime() > 0));
	W("search=" + (radar(Titanium) != null) + ";" + (radar(PowerCell) != null) + ";" + sizeof(radarall(Titanium)));
'''
LANG_EXTRA = r'''
int Fact(int n) { if (n <= 1) return 1; return n * Fact(n - 1); }
int Fib(int n) { int a = 0; int b = 1; int i; for (i = 0; i < n; i++) { int t = a + b; a = b; b = t; } return a; }
public class CbpAcc
{
	float total = 0; int n = 0;
	void Add(float v) { total += v; n++; }
	float Mean() { if (n == 0) return 0; return total / n; }
}
'''

STOCK_BODY = r'''
	// the stock example program tcell1-like actions, run as the stock ai file does them: grab the cube in front, turn, drop
	wait(1);
	int e; float t = abstime();
	e = grab(); W("grab_err=" + e); W("load=" + (load != null ? load.category : -1));
	e = turn(90); e = drop(); W("drop_err=" + e); W("load_after=" + (load != null ? load.category : -1));
	W("cycle_s=" + (abstime()-t));
	// the second bot runs the stock example program ai/tcell1.txt (grab, turn 90, drop, turn -180, grab, turn 90, drop):
	// the power cell in front of it ends up where the stock program puts it
	wait(8); object c = radar(PowerCell);
	W("stock_tcell1_cell=" + round(c.position.x*10)/10 + ";" + round(c.position.y*10)/10);
'''

CONVERT_BODY = r'''
	wait(1);
	int e; float t0 = abstime(); object o = radar(TitaniumOre); object conv = radar(Converter);
	W("ore_found=" + (o != null)); W("conv_found=" + (conv != null));
	e = goto(o.position, 0, 1); W("goto_ore_err=" + e);
	e = grab(); W("grab_err=" + e); W("load=" + (load != null ? load.category : -1));
	e = goto(conv.position); W("goto_conv_err=" + e);
	e = drop(); W("drop_err=" + e); W("t_dropped=" + (abstime()-t0));
	e = move(-2.5);
	object c = null; float t1 = abstime();
	while (abstime() - t1 < 40) { c = radar(Titanium, 0, 360, 0, 6); if (c != null) break; wait(0.25); }
	W("cube=" + (c != null)); W("convert_s=" + (abstime()-t1));
	if (c != null) { e = goto(c.position, 0, 1); e = grab(); W("_grab_cube_err=" + e); }
	W("ore_left=" + sizeof(radarall(TitaniumOre)));
'''

FACTORY_BODY = r'''
	wait(1);
	int e; object fac = radar(BotFactory); object cube = radar(Titanium);
	e = goto(cube.position, 0, 1); e = grab(); W("grab_err=" + e);
	e = goto(fac.position); W("goto_fac_err=" + e); e = drop(); W("drop_err=" + e);
	e = move(-5); e = move(-5);
	float seen = -1; float t0 = abstime(); e = factory(WheeledShooter, "", fac); wait(0.5); W("busy_after_start=" + isbusy(fac)); W("factory_err=" + e);
	object b = null;
	while (abstime() - t0 < 60) { b = radar(WheeledShooter, 0, 360, 0, 40); if (b != null and seen < 0) seen = abstime()-t0; if (not isbusy(fac)) break; wait(0.25); }
	W("built_s=" + (abstime()-t0)); W("bot=" + (b != null)); W("bot_seen_s=" + seen);
	if (b != null) { W("bot_cell=" + (b.energyCell != null)); W("bot_shield=" + b.shieldLevel); W("bot_dist_from_factory=" + round(distance2d(b.position, fac.position))); }
	W("titanium_left=" + (radar(Titanium) != null));
'''

BUILD_BODY = r'''
	wait(1);
	int e; object cube = radar(Titanium); W("cube_found=" + (cube != null));
	e = goto(cube.position, 0, 1, 1); W("goto_err=" + e);
	e = move(-3); float t0 = abstime();
	e = build(PowerStation); W("build_err=" + e); W("build_s=" + (abstime()-t0));
	object ps = radar(PowerStation, 0, 360, 0, 20); W("built=" + (ps != null));
	W("cube_left=" + (radar(Titanium) != null));
	W("energy_used=" + round((1 - energyCell.energyLevel)*1000)/1000);
'''

ENERGY_BODY = r'''
	wait(1);
	int e; object st = radar(PowerStation); W("station=" + (st != null));
	float e0 = energyCell.energyLevel; W("e0=" + round(e0*1000)/1000);
	e = goto(st.position); W("goto_err=" + e);
	float e1 = energyCell.energyLevel; W("e_arrive=" + round(e1*1000)/1000);
	float t0 = abstime(); while (abstime() - t0 < 6) wait(0.5);
	W("e_6s=" + round(energyCell.energyLevel*1000)/1000); W("gain_6s=" + round((energyCell.energyLevel - e1)*1000)/1000);
	W("station_energy=" + round(st.energyLevel*1000)/1000);
'''

FLIGHT_BODY = r'''
	wait(1);
	float t0 = abstime(); float z0 = position.z; W("alt0=" + round(altitude*100)/100);
	jet(1); wait(2); W("alt_2s=" + round(altitude*10)/10); W("temp_2s=" + round(temperature*100)/100);
	jet(0); wait(1); float a1 = altitude; wait(1); W("hover_drift=" + round(abs(altitude - a1)*10)/10);
	point g; g.x = position.x + 20; g.y = position.y; g.z = 0;
	float t1 = abstime(); int e = goto(g, 15); W("fly_goto_err=" + e); W("fly_goto_s=" + (abstime()-t1)); W("fly_goto_miss=" + distance2d(position, g));
	W("alt_after=" + round(altitude*10)/10);
	W("temp_after=" + round(temperature*100)/100);
	W("energy_used=" + round((1 - energyCell.energyLevel)*1000)/1000);
'''

ALIEN_OBSERVER_BODY = r'''
	// observer, 60 m away (out of the ant's 40 m radar): an ant (stock ai/antics.txt, patrol radius 15 m) next to a passive
	// grabber; after 25 s a shooter (cbp_alien_s.txt) hunts it. Bots near the ant may be destroyed, so only this one reports.
	wait(0.5); object tg = radar(WheeledGrabber, 0, 360, 0, 1000); object ant = radar(AlienAnt);
	W("setup=" + (tg != null) + ";" + (ant != null));
	float t0 = abstime(); bool hit = false; float first = -1;
	while (abstime() - t0 < 25) {
		tg = radar(WheeledGrabber, 0, 360, 0, 1000);
		if (not hit and (tg == null or tg.shieldLevel < 1)) { hit = true; first = abstime() - t0; }
		wait(0.2);
	}
	W("attacked=" + hit); W("first_hit_s=" + first);
	t0 = abstime(); float killed = -1;
	while (abstime() - t0 < 45) { if (radar(AlienAnt) == null) { killed = abstime() - t0; break; } wait(0.2); }
	W("ant_killed=" + (killed >= 0)); W("kill_s=" + killed);
'''
ALIEN_SHOOTER_BODY = r'''
	// shooter: waits for the 25 s attack window, then hunts the ant with aim/fire
	wait(25.5);
	float t0 = abstime(); object a;
	while (abstime() - t0 < 44) {
		a = radar(AlienAnt); if (a == null) break;
		float d = distance2d(position, a.position);
		if (d > 20) { point g; g.x = a.position.x + (position.x - a.position.x) * 12 / d; g.y = a.position.y + (position.y - a.position.y) * 12 / d; g.z = 0; goto(g, 0, 1, 1); W("approach=" + round(d)); continue; }
		turn(direction(a.position));
		d = distance2d(position, a.position); float dz = a.position.z - position.z;
		aim(atan(dz / d)); fire(1); W("shot=" + round(d) + ";" + round(dz));
	}
	W("shield=" + shieldLevel);
'''

RESEARCH_BODY = r'''
	wait(1);
	object rc = radar(ResearchCenter); W("rc=" + (rc != null));
	int e0; object cell = radar(PowerCell); e0 = goto(cell.position, 0, 1); e0 = grab(); W("grab_cell_err=" + e0);
	e0 = goto(rc.position); e0 = drop(); W("drop_cell_err=" + e0); e0 = move(-4);
	W("before=" + researched(ResearchTracked));
	float t0 = abstime(); int e = research(ResearchTracked, rc); W("research_err=" + e);
	while (abstime() - t0 < 60) { if (researched(ResearchTracked)) break; wait(0.25); }
	W("done=" + researched(ResearchTracked)); W("research_s=" + (abstime()-t0));
	if (rc.energyCell != null) W("rc_cell=" + round(rc.energyCell.energyLevel*1000)/1000);
'''

MISSION_BODY = r'''
	// carry the titanium cube onto the goal area: the level's EndMissionTake then wins the mission (the game exits)
	wait(1);
	int e; object c = radar(Titanium); e = goto(c.position, 0, 1); e = grab(); W("grab_err=" + e);
	point g; g.x = 10; g.y = -60; g.z = 0;
	e = goto(g); e = drop(); W("drop_err=" + e); W("t_drop=" + abstime());
	W("END");
	wait(60);
'''

ASTRO_WATCH_BODY = r'''
	// watcher: records the astronaut's position while the runner presses real keys: Shift 2 s (jetpack up), Ctrl 5 s (meant
	// to descend; with xdotool he keeps hovering, recorded as _after_ctrl_dz only), Up 3 s (moves forward, in the air)
	wait(3); object me = radar(Me); W("me=" + (me != null));
	point p0 = me.position; W("start=" + round(p0.x*10)/10 + ";" + round(p0.y*10)/10 + ";" + round(p0.z*10)/10);
	file g(); g.open("keys.go", "w"); g.writeln("1"); g.close();
	float t0 = abstime(); float maxz = p0.z; point pl = p0; float maxd = 0; int lost = 0;
	while (abstime() - t0 < 16) {
		me = radar(Me);
		if (me == null) { lost++; } else {
			float t = abstime() - t0;
			if (t < 4) { if (me.position.z > maxz) maxz = me.position.z; }
			else if (t < 8.6) { pl = me.position; }
			else { float d = distance2d(pl, me.position); if (d > maxd) maxd = d; }
		}
		wait(0.1);
	}
	W("jet_rise=" + round((maxz - p0.z)*10)/10); W("_after_ctrl_dz=" + round((pl.z - p0.z)*10)/10); W("walk_dist=" + round(maxd*10)/10);
	W("radar_lost=" + (lost > 0));
'''
ASTRO_KEYS = """sleep 0.3
keydown Shift_L
sleep 2
keyup Shift_L
shot astro_jet
keydown Control_L
sleep 5
keyup Control_L
sleep 0.5
keydown Up
sleep 3
keyup Up
shot astro_walk
sleep 1
"""

SAVE_BODY = r'''
	// a long-running program: counts and moves; its state (variables, position) must survive quicksave + quickload
	int n = 0; float t0 = abstime();
	while (true) {
		n++; W("tick=" + n + ";" + round(position.x*10)/10);
		if (n == 3) { file g(); g.open("keys.go", "w"); g.writeln("1"); g.close(); }
		if (n <= 2) move(3); else wait(1);
		if (n >= 14) break;
	}
'''
SAVE_KEYS = """sleep 1
key F5
sleep 3
shot saved
"""


def scene_obj(*lines): return "\n".join(lines)


SPOT = {"me": "-53.0; 39.0"}
PROBES = {
    "motion": dict(t=60, objects=[f"CreateObject pos=-30;{y} dir=0.0 type={cat} script1=\"cbp_motion_{k}.txt\" run=1 power=1.0"
                                   for k, (cat, y) in MOTION_CHASSIS.items()],
                   progs={f"cbp_motion_{k}.txt": cbot(f"motion_{k}", MOTION_BODY) for k in MOTION_CHASSIS},
                   tags=[f"motion_{k}" for k in MOTION_CHASSIS]),
    "cbot_lang": dict(t=30, objects=["CreateObject pos=-30;0 dir=0.0 type=WheeledGrabber script1=\"cbp_lang.txt\" run=1 power=0.8",
                                     "CreateObject pos=-20;5 dir=0.0 type=Titanium", "CreateObject pos=-20;-5 dir=0.0 type=Titanium",
                                     "CreateObject pos=-25;10 dir=0.0 type=PowerCell"],
                      progs={"cbp_lang.txt": cbot("lang", LANG_BODY) + LANG_EXTRA}, tags=["lang"]),
    "stock_program": dict(t=40, objects=["CreateObject pos=-30;0 dir=0.0 type=WheeledGrabber script1=\"cbp_stock.txt\" run=1 power=1.0",
                                         "CreateObject pos=-27.5;0 dir=0.0 type=Titanium",
                                         "CreateObject pos=-30;10 dir=0.0 type=WheeledGrabber script1=\"tcell1.txt\" run=1 power=1.0",
                                         "CreateObject pos=-27.5;10 dir=0.0 type=PowerCell"],
                          progs={"cbp_stock.txt": cbot("stock", STOCK_BODY)}, tags=["stock"]),
    "convert": dict(t=90, objects=["CreateObject pos=-30;0 dir=0.0 type=WheeledGrabber script1=\"cbp_convert.txt\" run=1 power=1.0",
                                   "CreateObject pos=-22;6 dir=0.0 type=TitaniumOre", "CreateObject pos=-15;-12 dir=0.0 type=Converter"],
                    progs={"cbp_convert.txt": cbot("convert", CONVERT_BODY)}, tags=["convert"]),
    "factory": dict(t=110, objects=["CreateObject pos=-30;0 dir=0.0 type=WheeledGrabber script1=\"cbp_factory.txt\" run=1 power=1.0",
                                    "CreateObject pos=-24;6 dir=0.0 type=Titanium", "CreateObject pos=-10;-15 dir=1.0 type=BotFactory"],
                    progs={"cbp_factory.txt": cbot("factory", FACTORY_BODY)}, tags=["factory"]),
    "build": dict(t=100, objects=["CreateObject pos=-30;0 dir=0.0 type=WheeledBuilder script1=\"cbp_build.txt\" run=1 power=1.0",
                                  "CreateObject pos=-18;0 dir=0.0 type=Titanium", "CreateObject pos=-30;-12 dir=0.0 type=Titanium"],
                  progs={"cbp_build.txt": cbot("build", BUILD_BODY)}, tags=["build"]),
    "energy": dict(t=60, objects=["CreateObject pos=-30;0 dir=0.0 type=WheeledGrabber script1=\"cbp_energy.txt\" run=1 power=0.2",
                                  "CreateObject pos=-15;-10 dir=1.0 type=PowerStation"],
                   progs={"cbp_energy.txt": cbot("energy", ENERGY_BODY)}, tags=["energy"]),
    "flight": dict(t=60, objects=["CreateObject pos=-30;0 dir=0.0 type=WingedGrabber script1=\"cbp_flight.txt\" run=1 power=1.0"],
                   progs={"cbp_flight.txt": cbot("flight", FLIGHT_BODY)}, tags=["flight"]),
    "aliens": dict(t=100, objects=["CreateObject pos=-20;0 dir=0.0 type=WheeledGrabber power=1.0",
                                   "CreateObject pos=-5;0 cmdline=-20;0;15 dir=0.0 type=AlienAnt script1=\"antics.txt\" run=1",
                                   "CreateObject pos=-45;-15 dir=0.0 type=WheeledShooter script1=\"cbp_alien_s.txt\" run=1 power=1.0 magnifyDamage=0.1",
                                   "CreateObject pos=-30;60 dir=0.0 type=TrackedSniffer script1=\"cbp_alien_o.txt\" run=1 power=1.0"],
                   progs={"cbp_alien_o.txt": cbot("aliens", ALIEN_OBSERVER_BODY), "cbp_alien_s.txt": cbot("alien_shooter", ALIEN_SHOOTER_BODY)},
                   tags=["aliens"]),
    "research": dict(t=90, objects=["CreateObject pos=-30;0 dir=0.0 type=WheeledGrabber script1=\"cbp_research.txt\" run=1 power=1.0",
                                    "CreateObject pos=-15;-10 dir=1.0 type=ResearchCenter", "CreateObject pos=-24;6 dir=0.0 type=PowerCell"],
                     progs={"cbp_research.txt": cbot("research", RESEARCH_BODY)}, tags=["research"], research="none"),
    "mission": dict(t=90, objects=["CreateObject pos=-30;-40 dir=0.0 type=WheeledGrabber script1=\"cbp_mission.txt\" run=1 power=1.0",
                                   "CreateObject pos=-25;-45 dir=0.0 type=Titanium"],
                    progs={"cbp_mission.txt": cbot("mission", MISSION_BODY)}, tags=["mission"], mission=True),
    "astronaut": dict(t=70, objects=["CreateObject pos=-40;20 dir=0.0 type=WheeledGrabber script1=\"cbp_astro.txt\" run=1 power=1.0 selectable=0"],
                      progs={"cbp_astro.txt": cbot("astro", ASTRO_WATCH_BODY)}, tags=["astro"], keys=ASTRO_KEYS),
}
for i, n in enumerate(PROBES): PROBES[n]["level"] = i + 1


def build_scene(name, p):
    objs = [f"CreateObject pos={SPOT['me']} dir=0.2 type=Me"] + p["objects"]
    research = "" if p.get("research") == "none" else ALL_RESEARCH
    if p.get("research") == "none":
        research = "EnableResearch type=TRACKER\n" + "\n".join(f"EnableBuild type={b}" for b in ["BotFactory", "ResearchCenter"])
    ending = ""
    if p.get("mission"):
        ending = "EndMissionTake pos=10.00;-60.00 dist=4.00 type=Titanium min=1"
    head = HEAD.replace("EndingFile win=-1 lost=-1", "EndingFile win=-1 lost=-1" if not p.get("mission") else "")
    return head.format(name=name, research=research, objects="\n".join(objs), ending=ending)


# ------------------------------------------------------------------ host side -------------------------------------------------------------
TRANSIENT = re.compile(r"EXEC_STREAM_QUOTA_EXCEEDED|exec stream connection rejected|No such exec")


def put_files(seat, files, tries=10):
    """{sandbox path: text} -> written in the sandbox as one tar (seat.put + tar -x); retried on a rejected exec stream"""
    for i in range(tries):
        try: return _put_files(seat, files)
        except RuntimeError as e:
            if i == tries - 1 or not TRANSIENT.search(str(e)): raise
            time.sleep(3)


def _put_files(seat, files):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for path, text in files.items():
            b = text.encode(); ti = tarfile.TarInfo(path.lstrip("/")); ti.size = len(b); ti.mode = 0o755 if path.endswith(".sh") else 0o644
            tf.addfile(ti, io.BytesIO(b))
    tmp = f"{seat.home}/.cbp_{os.getpid()}_{int(time.time() * 1000)}.tgz"
    seat.put(tmp, buf.getvalue())
    seat.exec(f"tar -xzf {tmp} -C / && rm -f {tmp}", check=True)


def build_probe_mod(seat, root, display, pm, runner):
    """(Re)writes the gaprobe data mod (dir pm) and the runner script into the sandbox (per probe instance: seats share the install)"""
    files = {runner: RUN_SH.replace("__ROOT__", root).replace("__DISPLAY__", display),
             f"{pm}/levels/challenges/chapter009/chaptertitle.txt": 'Title.E text="GameAnything probes" resume="probe levels"\n'}
    for n, p in PROBES.items():
        files[f"{pm}/levels/challenges/chapter009/level{p['level']:03d}/scene.txt"] = build_scene(n, p)
        for fn, src in p["progs"].items(): files[f"{pm}/ai/{fn}"] = src
        if p.get("keys"): files[f"{pm}/keys_{n}.txt"] = p["keys"]
    files[f"{pm}/keys_save.txt"] = SAVE_KEYS
    files[f"{pm}/ai/cbp_save.txt"] = cbot("save", SAVE_BODY)
    seat.exec(f"rm -rf {pm}", check=True)
    put_files(seat, files)


ERR = re.compile(r"^\[ERROR\]|Unhandled exception|SIGSEGV|segmentation fault|CBot.*error|terminate called", re.I)
# vanilla noise of this build in this pod (present in every vanilla run): missing shadow textures in the distro data, no audio device
ERR_IGNORE = re.compile(r"Unable to read list of finished|shadow\d\d\.png|Could not open audio device|research\.gam|Couldn't load texture 'textures/shadow", re.I)


def log_errors(text):
    return len([l for l in text.splitlines() if ERR.search(l) and not ERR_IGNORE.search(l)])


class CBProbes:
    def __init__(self, seat, root, name, mods=(), display=None, keep=False):
        self.seat, self.root, self.name, self.mods, self.keep = seat, root, name, [Path(m) for m in mods], keep
        self.display = display or seat.display
        self.inst = f"{root}/inst/{name}"; self.pm = f"{self.inst}/_probe_mod"
        self.runs = []

    def dx(self, cmd, timeout=60, check=False):
        # the sandbox's exec streams are shared with other agents: wait and retry when remote docker rejects a stream (as the original)
        for i in range(40):
            r = self.seat.exec(cmd, timeout=timeout)
            if not TRANSIENT.search((r.stderr or "") + (r.stdout or "")[-300:]): break
            time.sleep(3)
        if check and r.returncode: raise RuntimeError(f"exec failed ({r.returncode}): {cmd[:120]} {r.stderr[-300:]}")
        return r.stdout

    def put(self, path, text): put_files(self.seat, {path: text})

    def _prepare(self, sub):
        s = f"{self.inst}/{sub}"
        names, en = ["gaprobe"], ["1"]
        cmd = f"rm -rf {s}; mkdir -p {s}/mods {s}/files; cp -r {self.pm} {s}/mods/gaprobe"
        for m in self.mods:
            names.append(m.name); en.append("1")
            cmd += f"; cp -r {self.inst}/_mods/{m.name} {s}/mods/{m.name}"
        self.dx(cmd, check=True)
        self.put(f"{s}/colobot.ini", INI.format(names=",".join(names), enabled=",".join(en)))
        return s

    def _upload_mods(self):
        self.dx(f"rm -rf {self.inst}/_mods; mkdir -p {self.inst}/_mods", check=True)
        for m in self.mods: self.seat.copy_in(m, f"{self.inst}/_mods/{m.name}")

    def _run(self, sub, scene, timeout, tags, keys="", env="", read=()):
        s = f"{self.inst}/{sub}"
        # detached launch (setsid -f: the exec returns at once); the runner writes `launched` (retry if it did not)
        for attempt in range(3):
            self.dx(f"cd {s} && {env} setsid -f {self.inst}/run_cb.sh {s} {scene} {timeout} {','.join(tags)} '{keys}' > {s}/runner.log 2>&1 < /dev/null", timeout=20)
            for _ in range(10):
                if self.dx(f"cat {s}/launched 2>/dev/null").strip(): break
                time.sleep(1)
            else: continue
            break
        t0 = time.time()
        while time.time() - t0 < timeout + 30 + (300 if keys else 0):   # keyboard runs may first wait for the seat's keyboard lock
            try: out = self.dx(f"cat {s}/exit.json 2>/dev/null")
            except Exception: out = ""
            if out.strip(): break
            time.sleep(2)
        ex = json.loads(out) if out.strip() else {"done": False, "alive_at_end": None, "error": "runner did not finish"}
        res = {}
        for t in list(tags) + list(read):
            txt = self.dx(f"cat {s}/files/{t}.txt 2>/dev/null")
            res[t] = parse_kv(txt)
        lg = self.dx(f"cat {s}/game.log 2>/dev/null", timeout=60)
        errs = [l for l in lg.splitlines() if ERR.search(l) and not ERR_IGNORE.search(l)]
        crashed = bool(re.search(r"Unhandled exception|SIGSEGV", lg))
        self.runs.append({"name": sub, "done": ex.get("done"), "errors": len(errs), "crashed": crashed, "_lines": errs[:60],
                          "wall_s": ex.get("wall_s")})
        return res, ex, lg

    def probe(self, n):
        p = PROBES[n]
        keys = f"{self.pm}/keys_{n}.txt" if p.get("keys") else ""
        self._prepare(n)
        res, ex, lg = self._run(n, f"challenges9{p['level']:02d}", p["t"], p["tags"], keys, read=p.get("read", []))
        out = {}
        for t in p["tags"] + p.get("read", []):
            r = res[t]
            if not r.get("END") and t in p["tags"]: r["_incomplete"] = True
            r.pop("END", None)
            out[t.replace(n + "_", "").replace("alien_", "") if len(p["tags"] + p.get("read", [])) > 1 else "_"] = r
        if "_" in out: out = out["_"]
        if n == "mission":
            out["mission_won_exit"] = bool(re.search(r"Mission finished in single mission mode", lg))
            out["game_exited_by_itself"] = ex.get("alive_at_end") is False
        if not ex.get("done") and n != "mission": out["_timeout"] = True
        return out

    def stock_levels(self, scenes=("missions101", "missions201", "missions301", "exercises101", "challenges101", "free101", "battles101", "plus101")):
        """every stock level in the list loads (no level-load error), the game keeps running for 8 s"""
        out = {}
        for sc in scenes:
            sub = f"stock_{sc}"
            self._prepare(sub)
            _, ex, lg = self._run(sub, sc, 14, ["never"])
            m = re.search(r"Loading level: (\S+)", lg)
            out[sc] = {"loaded": bool(m) and not re.search(r"error occurred while trying to load a level|Failed to open file: levels", lg),
                       "alive_after_14s": bool(ex.get("alive_at_end")),
                       "load_errors": len([l for l in lg.splitlines() if ERR.search(l) and not ERR_IGNORE.search(l)])}
        return out

    def save_load(self):
        """a running program and its bot: quicksave (F5) mid-run, then a fresh game process loads the save (-loadsave) and the
        program must continue where it was (tick counter, position)"""
        sub = "save"
        s = self._prepare(sub)
        scene = PROBES_SAVE_SCENE
        self.put(f"{s}/mods/gaprobe/levels/challenges/chapter009/level099/scene.txt", scene)
        res, ex, lg = self._run(sub, "challenges999", 40, ["save"], f"{self.pm}/keys_save.txt")
        first = res["save"]
        saves = self.dx(f"ls -d {s}/savegame/*/* 2>/dev/null").split()
        out = {"saved": bool(saves), "ticks_before": first.get("_ticks", [])}
        if not saves: return dict(out, error="no quicksave written")
        rel = os.path.relpath(saves[0], f"{s}/savegame")
        objs = self.dx(f"grep -c '^CreateObject' {saves[0]}/data.sav 2>/dev/null").strip()
        out["saved_objects"] = int(objs) if objs.isdigit() else None
        self.dx(f"cp {s}/files/save.txt {s}/files/save_first.txt; rm -f {s}/files/save.txt {s}/files/keys.go {s}/files/keys.done {s}/exit.json {s}/launched {s}/pid")
        res2, ex2, lg2 = self._run(sub, "-", 45, ["save"], "", env=f"CB_LOADSAVE={shlex.quote(rel)}")
        out["ticks_after_load"] = res2["save"].get("_ticks", [])
        out["loaded_finished"] = bool(res2["save"].get("END"))
        return out

    def run(self, names=None):
        build_probe_mod(self.seat, self.root, self.display, self.pm, f"{self.inst}/run_cb.sh")
        self._upload_mods()
        names = names or ALL
        out, timing = {}, {}
        for n in names:
            t = time.time()
            try:
                if n == "stock_levels": out[n] = self.stock_levels()
                elif n == "save_load": out[n] = self.save_load()
                else: out[n] = self.probe(n)
            except Exception as ex:
                out[n] = {"error": f"{type(ex).__name__}: {str(ex)[:300]}"}
            timing[n] = round(time.time() - t, 1)
        out["log_errors"] = {"runs": len(self.runs), "not_finished": sorted(r["name"] for r in self.runs if not r["done"] and not r["name"].startswith(("stock_", "save", "mission"))),
                             "crashed": sorted(r["name"] for r in self.runs if r["crashed"]),
                             "errors": sum(r["errors"] for r in self.runs), "_per_run": {r["name"]: r["errors"] for r in self.runs},
                             "_lines": {r["name"]: r["_lines"] for r in self.runs if r["_lines"]}}
        out["_timing"] = timing
        if not self.keep: self.dx(f"rm -rf {self.inst}", timeout=120)
        return out


PROBES_SAVE_SCENE = HEAD.format(name="save", research=ALL_RESEARCH, ending="",
                                objects=f"CreateObject pos={SPOT['me']} dir=0.2 type=Me\n"
                                        "CreateObject pos=-30;0 dir=0.0 type=WheeledGrabber script1=\"cbp_save.txt\" run=1 power=1.0")

ALL = list(PROBES) + ["stock_levels", "save_load"]


def parse_kv(txt):
    d, ticks = {}, []
    for l in txt.splitlines():
        l = l.strip()
        if l == "END": d["END"] = True; continue
        if "=" not in l: continue
        k, v = l.split("=", 1)
        if k == "tick": ticks.append(v); continue
        d[k] = _num(v)
    if ticks: d["_ticks"] = ticks
    return d


def _num(v):
    if ";" in v: return [_num(x) for x in v.split(";")]
    try:
        f = float(v); return int(f) if f.is_integer() and "." not in v else round(f, 3)
    except ValueError:
        return {"true": True, "false": False}.get(v, v)


# ------------------------------------------------------------------ compare ---------------------------------------------------------------
def _close(a, b, rel, ab):
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return abs(a - b) <= max(ab, rel * abs(a))
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return all(_close(x, y, rel, ab) for x, y in zip(a, b))
    return a == b


# per-key tolerances (rel, abs) from the A/A runs; keys not listed must be equal
TOL = {
    "motion": {"_s": (0.15, 0.3), "_dist": (0.05, 0.3), "goto_miss": (0, 0.5), "energy_used": (0.25, 0.005), "start": (0, 0.2), "end": (0, 1.0),
               "turn_orient": (0, 2.0)},
    "convert": {"t_dropped": (0.2, 2.0), "convert_s": (0.2, 2.0)},
    "factory": {"built_s": (0.15, 2.0), "bot_seen_s": (0.15, 1.0), "bot_dist_from_factory": (0, 2)},
    "build": {"build_s": (0.15, 1.5), "energy_used": (0.3, 0.01)},
    "energy": {"e_arrive": (0, 0.02), "e_6s": (0, 0.03), "gain_6s": (0.25, 0.02), "station_energy": (0, 0.05)},
    "flight": {"alt_2s": (0.15, 1.0), "temp_2s": (0.2, 0.05), "hover_drift": (0, 1.0), "fly_goto_s": (0.2, 1.5), "fly_goto_miss": (0, 1.5),
               "alt_after": (0, 1.5), "temp_after": (0.3, 0.1), "energy_used": (0.3, 0.01)},
    "research": {"research_s": (0.1, 1.5), "rc_cell": (0, 0.05)},
    "mission": {"t_drop": (0.2, 3.0)},
    "stock_program": {"cycle_s": (0.15, 0.5), "stock_tcell1_cell": (0, 0.6)},
    "astronaut": {"walk_dist": (0.15, 1.0), "jet_rise": (0.25, 1.5), "start": (0, 0.5)},
}
IGNORE = {"aliens": {"first_hit_s", "kill_s"}}   # random (ant AI uses rand()): outcome only


def _cmp_dict(name, b, g, tol, ignore=()):
    d = {}
    for k in sorted(set(b) | set(g)):
        if k.startswith("_") or k in ignore: continue
        bv, gv = b.get(k), g.get(k)
        if isinstance(bv, dict) or isinstance(gv, dict):
            sub = _cmp_dict(name, bv or {}, gv or {}, tol, ignore)
            d.update({f"{k}.{kk}": vv for kk, vv in sub.items()}); continue
        t = next((v for suf, v in tol.items() if k == suf or (suf.startswith("_") and k.endswith(suf))), None)
        ok = _close(bv, gv, *t) if t else bv == gv
        if not ok: d[k] = f"{bv} -> {gv}"
    for k in ("_incomplete", "_timeout"):
        if g.get(k) and not b.get(k): d[k] = "probe did not finish"
    return d


def compare(name, base, got):
    """-> (ok | None, detail)"""
    if not isinstance(got, dict) or not got: return None, "not probed"
    if "error" in got: return None, got["error"]
    if not isinstance(base, dict) or "error" in base: return None, f"baseline: {(base or {}).get('error')}"
    if name == "log_errors":
        if got.get("crashed"): return False, f"crashed in {got['crashed']}"
        nf = [x for x in got.get("not_finished", []) if x not in base.get("not_finished", [])]
        ok = got.get("errors", 0) <= base.get("errors", 0) and not nf
        return ok, f"{got.get('errors')} error lines vs vanilla {base.get('errors')}" + (f"; not finished: {nf}" if nf else "")
    if name == "save_load":
        tb, ta = got.get("ticks_before") or [], got.get("ticks_after_load") or []
        d = {}
        if not got.get("saved"): d["saved"] = "no quicksave written"
        if not got.get("loaded_finished"): d["loaded_finished"] = "the loaded game did not run the saved program to its end"
        if not ta or int(str(ta[0]).split(";")[0]) <= 1: d["resumed"] = f"program restarted instead of resuming: {ta[:3]}"
        else:
            bt = {str(x).split(";")[0]: x for x in tb}
            bad = [x for x in ta if bt.get(str(x).split(";")[0]) != x]
            if bad: d["state"] = f"after load {bad[:3]} differ from the same ticks before the save"
        if base.get("saved_objects") is not None and got.get("saved_objects") != base.get("saved_objects"): d["saved_objects"] = f"{base.get('saved_objects')} -> {got.get('saved_objects')}"
        return not d, d or f"resumed at tick {ta[0] if ta else None}, same state as before the save"
    d = _cmp_dict(name, base, got, TOL.get(name, {}), IGNORE.get(name, ()))
    return not d, d or "same as vanilla"


def item_result(spec, base, got):
    if spec == "log_errors": return compare("log_errors", base.get("log_errors"), got.get("log_errors"))
    p = spec.split(".")[0]
    b, g = base.get(p), got.get(p)
    if "." in spec and isinstance(b, dict) and isinstance(g, dict):
        fields = spec.split(".", 1)[1].split("+")
        b = {k: v for k, v in b.items() if k in fields}; g = {k: v for k, v in g.items() if k in fields or k == "error"}
    return compare(p, b, g)
