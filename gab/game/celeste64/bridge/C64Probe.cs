// GameAnything probe harness for Celeste 64 v1.1.1 (pool: contracts/pool_celeste64.yaml, module eval/probes_celeste64.py).
// Inactive unless the env var C64_PROBE is set. Hook: Program.Main calls `if (C64Probe.Active) App.Register<C64Probe>();`
// before App.Run<Game>, so this module's Update runs first every fixed 60 Hz frame. It drives the game only through the
// game's own input path (Foster Input.OnKey -> Controls) plus teleports / reads via reflection, and prints one line
//   C64PROBE {"probe": name, ...}
// per probe to stdout (also appended to $C64_PROBE_OUT). Screenshots of the game's render target go to $C64_PROBE_SHOTS.
// State server (GameAnything-Bench gab, 2026-10-31; STATE_SPEC): with $C64_STATE=<dir> (and no $C64_PROBE) the module drives
// nothing; every 6th frame it answers <dir>/req with <dir>/state.json (engine fields, what mods added inside the engine, the
// text on screen: UI.Text calls of the last 300 ms through the one-line hook in UI.Text + the visible dialog line).
// $C64_VANILLA = a file of "type|map|level|flag NAME" lines (the vanilla game) so mod additions are told apart generically.
using System.Globalization;
using System.Reflection;
using System.Text;

namespace Celeste64;

public class C64Probe : Foster.Framework.Module
{
	public static bool Active => !string.IsNullOrEmpty(Environment.GetEnvironmentVariable("C64_PROBE")) || !string.IsNullOrEmpty(Environment.GetEnvironmentVariable("C64_STATE"));
	const BindingFlags BF = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance;
	const string Arena = "zz_probe_arena";

	static readonly MethodInfo OnKey = typeof(Input).GetMethod("OnKey", BindingFlags.NonPublic | BindingFlags.Static)!;
	readonly HashSet<Keys> held = new();
	readonly HashSet<Keys> sent = new();
	IEnumerator<int>? main;
	string outPath = "", shotDir = "";
	Dictionary<string, object?> cur = new();
	readonly List<string> logLines = new();
	int frame;

	public override void Startup()
	{
		outPath = Environment.GetEnvironmentVariable("C64_PROBE_OUT") ?? "";
		shotDir = Environment.GetEnvironmentVariable("C64_PROBE_SHOTS") ?? "";
		stateDir = Environment.GetEnvironmentVariable("C64_STATE") ?? "";
		if (stateDir != "") { textsOn = true; LoadVanilla(); }
		var names = (Environment.GetEnvironmentVariable("C64_PROBE") ?? "").Split(',', StringSplitOptions.RemoveEmptyEntries);
		if (names.Length > 0) main = Main(names).GetEnumerator();
	}

	public override void Update()
	{
		frame++;
		// presentation only: the game turns VSync on, and VSync on the pod's headless GPU X server stalls each swap
		if (App.VSync && Environment.GetEnvironmentVariable("C64_VSYNC") != "1") App.VSync = false;
		bool more;
		try { more = main != null && main.MoveNext(); }
		catch (Exception e) { Emit(new() { ["probe"] = "_fatal", ["error"] = e.ToString() }); more = false; }
		// held keys -> Foster input (visible to the game from the next frame on)
		foreach (var k in held) if (!sent.Contains(k)) OnKey.Invoke(null, [(int)k, (byte)1]);
		foreach (var k in sent) if (!held.Contains(k)) OnKey.Invoke(null, [(int)k, (byte)0]);
		sent.Clear(); sent.UnionWith(held);
		if (!more && main != null) { main = null; Emit(new() { ["probe"] = "_done", ["frames"] = frame }); App.Exit(); }
		if (stateDir != "" && frame % 6 == 0) ServeState();
	}

	// ------------------------------------------------------------ runner ------------------------------------------------------------
	IEnumerable<int> Main(string[] names)
	{
		foreach (var _ in WaitUntil(() => Top is Titlescreen && !Game.Instance.IsMidTransition, 1200)) yield return 0;
		foreach (var name in names)
		{
			cur = new() { ["probe"] = name };
			var t0 = frame;
			var m = GetType().GetMethod("P_" + name, BF);
			if (m == null) { cur["error"] = "unknown probe"; Emit(cur); continue; }
			var it = ((IEnumerable<int>)m.Invoke(this, null)!).GetEnumerator();
			while (true)
			{
				bool more;
				try { more = it.MoveNext(); }
				catch (Exception e) { cur["error"] = e.GetType().Name + ": " + e.Message; Log.Info("C64PROBE exception " + e); more = false; }
				if (!more) break;
				yield return 0;
			}
			held.Clear();
			cur["_frames"] = frame - t0;
			Emit(cur);
			yield return 0;
		}
	}

	void Emit(Dictionary<string, object?> d)
	{
		var line = "C64PROBE " + Json(d);
		Console.WriteLine(line);
		if (outPath != "") File.AppendAllText(outPath, line + "\n");
	}

	// ------------------------------------------------------------ helpers ------------------------------------------------------------
	static object? F(object o, string n)
	{
		var t = o.GetType();
		while (t != null)
		{
			var f = t.GetField(n, BF); if (f != null) return f.GetValue(o);
			var p = t.GetProperty(n, BF); if (p != null) return p.GetValue(o);
			t = t.BaseType;
		}
		throw new MissingMemberException(o.GetType().Name, n);
	}
	static void S(object o, string n, object? v)
	{
		var t = o.GetType();
		while (t != null)
		{
			var f = t.GetField(n, BF); if (f != null) { f.SetValue(o, v); return; }
			var p = t.GetProperty(n, BF); if (p != null && p.CanWrite) { p.SetValue(o, v); return; }
			t = t.BaseType;
		}
		throw new MissingMemberException(o.GetType().Name, n);
	}
	static Scene? Top => ((Stack<Scene>)F(Game.Instance, "scenes")!).TryPeek(out var s) ? s : null;
	static World? W => Top as World;
	static Player? P => W?.Get<Player>();
	static string St(Player p) => F(F(p, "stateMachine")!, "State")!.ToString()!;
	static void SetSt(Player p, string state)
	{
		var sm = F(p, "stateMachine")!;
		var prop = sm.GetType().GetProperty("State", BF)!;
		prop.SetValue(sm, Enum.Parse(Nullable.GetUnderlyingType(prop.PropertyType) ?? prop.PropertyType, state));
	}
	static Vec3 Vel(Player p) => p.Velocity;
	static bool Ground(Player p) => (bool)F(p, "onGround")!;
	static int Dashes(Player p) => p.Dashes;
	static float R(float v, int d = 2) => MathF.Round(v, d);
	static float[] V(Vec3 v) => [R(v.X), R(v.Y), R(v.Z)];
	static float HSpeed(Vec3 v) => new Vec2(v.X, v.Y).Length();

	IEnumerable<int> Wait(int n) { for (int i = 0; i < n; i++) yield return 0; }
	IEnumerable<int> WaitUntil(Func<bool> cond, int max)
	{
		for (int i = 0; i < max; i++) { bool ok; try { ok = cond(); } catch { ok = false; } if (ok) yield break; yield return 0; }
	}
	IEnumerable<int> Tap(Keys k, int frames = 2) { held.Add(k); foreach (var _ in Wait(frames)) yield return 0; held.Remove(k); foreach (var _ in Wait(2)) yield return 0; }

	/// <summary>Enter a map directly (as LevelInfo.Enter does, without the wipe). Returns via cur["_load_error"] on failure.</summary>
	IEnumerable<int> Enter(string map, string checkpoint = "", bool submap = false, string levelId = "0_FORSAKEN_CITY")
	{
		Save.Instance.LevelID = levelId;
		World? w = null; string? err = null;
		try { w = new World(new(map, checkpoint, submap, World.EntryReasons.Entered)); }
		catch (Exception e) { err = e.GetType().Name + ": " + e.Message; }
		if (w == null) { cur["_load_error"] = err; yield break; }
		foreach (var _ in WaitUntil(() => !Game.Instance.IsMidTransition, 600)) yield return 0;
		Game.Instance.Goto(new Transition() { Mode = Transition.Modes.Replace, Scene = () => w, StopMusic = true });
		foreach (var _ in WaitUntil(() => Top == w && !Game.Instance.IsMidTransition && P != null, 600)) yield return 0;
		foreach (var _ in Wait(10)) yield return 0;
	}

	/// <summary>Put the player at a spot, standing still in the Normal state, facing +Y with the camera behind (+Y = "Up" key).</summary>
	IEnumerable<int> Reset(Vec3 pos, int settle = 20)
	{
		held.Clear();
		foreach (var _ in Wait(2)) yield return 0;
		var p = P!;
		SetSt(p, "Normal");
		p.Position = pos; p.Stop();
		S(p, "dashes", 1); S(p, "tDashCooldown", 0f); S(p, "tDashResetCooldown", 0f); S(p, "tClimbCooldown", 0f);
		S(p, "targetFacing", Vec2.UnitY); S(p, "cameraTargetForward", new Vec3(0, 1, 0));
		p.Facing = Vec2.UnitY;
		foreach (var _ in Wait(settle)) yield return 0;
	}

	void Shot(string name)
	{
		if (shotDir == "") return;
		try
		{
			var target = (Target)F(Game.Instance, "target")!;
			var tex = target.Attachments[0];
			var px = new Color[tex.Width * tex.Height];
			tex.GetData<Color>(px);
			var flip = new Color[px.Length];
			for (int y = 0; y < tex.Height; y++) Array.Copy(px, y * tex.Width, flip, (tex.Height - 1 - y) * tex.Width, tex.Width);
			for (int i = 0; i < flip.Length; i++) flip[i].A = 255;
			Directory.CreateDirectory(shotDir);
			new Image(tex.Width, tex.Height, flip).WritePng(Path.Join(shotDir, name + ".png"));
		}
		catch (Exception e) { Log.Info("C64PROBE shot failed " + e.Message); }
	}

	static Dictionary<string, object?> Counts(World w)
	{
		var d = new SortedDictionary<string, int>();
		foreach (var a in w.Actors)
		{
			var n = a.GetType().Name;
			if (n is "Sound" or "Dust" or "Debris" or "Snow") continue;
			d[n] = d.GetValueOrDefault(n) + 1;
		}
		return d.ToDictionary(kv => kv.Key, kv => (object?)kv.Value);
	}

	// ------------------------------------------------------------ probes ------------------------------------------------------------
	/// fps: updates per wall second on the title screen (diagnostic, not a pool item)
	IEnumerable<int> P_fps()
	{
		var sw = System.Diagnostics.Stopwatch.StartNew();
		foreach (var _ in Wait(300)) yield return 0;
		cur["updates_per_s"] = Math.Round(300 / sw.Elapsed.TotalSeconds, 1);
	}

	/// boot: content the game loaded (levels list, maps, asset counts) and the title screen.
	IEnumerable<int> P_boot()
	{
		Shot("title");
		cur["version"] = Game.VersionString;
		cur["levels"] = Assets.Levels.Select(l => (object?)new Dictionary<string, object?> { ["id"] = l.ID, ["name"] = l.Name, ["map"] = l.Map, ["strawberries"] = l.Strawberries }).ToList();
		cur["maps"] = Assets.Maps.Keys.Where(k => k != Arena).OrderBy(k => k, StringComparer.Ordinal).Select(k => (object?)k).ToList();
		cur["n_textures"] = Assets.Textures.Count; cur["n_models"] = Assets.Models.Count; cur["n_subtextures"] = Assets.Subtextures.Count;
		cur["n_shaders"] = Assets.Shaders.Count; cur["n_fonts"] = Assets.Fonts.Count; cur["languages"] = Assets.Languages.Keys.OrderBy(k => k).Select(k => (object?)k).ToList();
		yield break;
	}

	/// hub: title -> overworld -> level card -> enter the first level, with the Confirm key only (as a player would).
	IEnumerable<int> P_hub()
	{
		var seen = new List<object?>();
		void Note() { var n = Top?.GetType().Name; if (seen.Count == 0 || (string?)seen[^1] != n) seen.Add(n); }
		Note();
		for (int i = 0; i < 12 && !(W != null && P != null && !Game.Instance.IsMidTransition); i++)
		{
			foreach (var _ in Tap(Keys.C)) { Note(); yield return 0; }
			for (int j = 0; j < 90; j++) { Note(); yield return 0; }
			if (Top is Overworld && i == 1) Shot("overworld");
		}
		foreach (var _ in WaitUntil(() => W != null && P != null && !Game.Instance.IsMidTransition, 600)) { Note(); yield return 0; }
		foreach (var _ in Wait(30)) yield return 0;
		Note();
		cur["scenes"] = seen;
		cur["entered"] = W != null && P != null;
		cur["map"] = W?.Entry.Map; cur["level_id"] = Save.Instance.LevelID;
		cur["player_state"] = P != null ? St(P) : null;
		Shot("level_start");
	}

	/// maps: every map file loads into a world, the player spawns and stands; entity counts per actor type.
	IEnumerable<int> P_maps()
	{
		var res = new Dictionary<string, object?>();
		foreach (var name in Assets.Maps.Keys.Where(k => k != Arena && !k.Equals("Palette", StringComparison.OrdinalIgnoreCase)).OrderBy(k => k, StringComparer.Ordinal).ToList())
		{
			cur.Remove("_load_error");
			foreach (var _ in Enter(name, "", name != "1")) yield return 0;
			var d = new Dictionary<string, object?>();
			if (cur.TryGetValue("_load_error", out var le)) { d["loaded"] = false; d["error"] = le; res[name] = d; continue; }
			var w = W; var p = P;
			d["loaded"] = w != null && w.Entry.Map == name;
			d["player"] = p != null;
			if (w != null && p != null)
			{
				foreach (var _ in Wait(120)) yield return 0;
				p = P;
				d["counts"] = Counts(w);
				d["checkpoints"] = w.All<Checkpoint>().Select(c => (object?)((Checkpoint)c).Name).OrderBy(s => (string?)s).ToList();
				d["player_alive"] = p != null && !p.Dead;
				d["player_state"] = p != null ? St(p) : null;
			}
			res[name] = d;
		}
		cur["maps"] = res;
	}

	/// move: running, jumping, falling in the probe arena (flat floor at z=0, wall face at y=200).
	IEnumerable<int> P_move()
	{
		foreach (var _ in Enter(Arena, "", false, "_PROBE")) yield return 0;
		if (P == null) { cur["error"] = "arena did not load: " + cur.GetValueOrDefault("_load_error"); yield break; }
		Shot("arena");
		var start = new Vec3(0, -150, 1);
		// run: hold Up 1.5 s, then release
		foreach (var _ in Reset(start)) yield return 0;
		var p = P!; var p0 = p.Position; float top = 0; int to95 = -1; float d1s = 0;
		held.Add(Keys.Up);
		for (int i = 1; i <= 90; i++)
		{
			yield return 0; p = P!;
			var hs = HSpeed(Vel(p)); top = MathF.Max(top, hs);
			if (to95 < 0 && hs >= 0.95f * 64) to95 = i;
			if (i == 60) d1s = (p.Position - p0).XY().Length();
		}
		held.Remove(Keys.Up);
		var pr = P!.Position; int stopF = -1;
		for (int i = 1; i <= 60; i++) { yield return 0; if (stopF < 0 && HSpeed(Vel(P!)) < 0.01f) stopF = i; }
		cur["run_top_speed"] = R(top); cur["run_frames_to_95pct"] = to95; cur["run_dist_1s"] = R(d1s);
		cur["stop_frames"] = stopF; cur["stop_dist"] = R((P!.Position - pr).XY().Length());
		// full jump: hold jump 0.6 s from standing
		foreach (var _ in Reset(start)) yield return 0;
		var (h, air) = (0f, 0);
		{
			var z0 = P!.Position.Z; bool left = false; held.Add(Keys.C);
			for (int i = 1; i <= 180; i++)
			{
				yield return 0; p = P!;
				if (i == 36) held.Remove(Keys.C);
				h = MathF.Max(h, p.Position.Z - z0);
				if (!Ground(p)) left = true;
				if (left && Ground(p)) { air = i; break; }
			}
			held.Remove(Keys.C);
		}
		cur["jump_height"] = R(h); cur["jump_air_frames"] = air;
		// short hop: tap jump for one frame
		foreach (var _ in Reset(start)) yield return 0;
		{
			var z0 = P!.Position.Z; float hh = 0; held.Add(Keys.C); yield return 0; held.Remove(Keys.C);
			for (int i = 1; i <= 120; i++) { yield return 0; hh = MathF.Max(hh, P!.Position.Z - z0); if (i > 5 && Ground(P!)) break; }
			cur["hop_height"] = R(hh);
		}
		// running jump: distance covered by a full jump at top speed
		foreach (var _ in Reset(start)) yield return 0;
		{
			held.Add(Keys.Up); foreach (var _ in Wait(40)) yield return 0;
			var a = P!.Position; held.Add(Keys.C); bool left = false; float dist = 0;
			for (int i = 1; i <= 180; i++)
			{
				yield return 0; p = P!;
				if (i == 36) held.Remove(Keys.C);
				if (!Ground(p)) left = true;
				if (left && Ground(p)) { dist = (p.Position - a).XY().Length(); break; }
			}
			held.Clear();
			cur["run_jump_dist"] = R(dist);
		}
		// fall from 150 units: terminal speed and time to land
		foreach (var _ in Reset(start + new Vec3(0, 0, 150), 1)) yield return 0;
		{
			float vmin = 0; int land = -1;
			for (int i = 1; i <= 300; i++) { yield return 0; vmin = MathF.Min(vmin, Vel(P!).Z); if (Ground(P!)) { land = i; break; } }
			cur["fall_terminal"] = R(vmin); cur["fall_frames_150"] = land;
		}
	}

	/// dash: ground dash speed / time / distance, dash count, refill on landing, no second air dash.
	IEnumerable<int> P_dash()
	{
		foreach (var _ in Enter(Arena, "", false, "_PROBE")) yield return 0;
		if (P == null) { cur["error"] = "arena did not load"; yield break; }
		var start = new Vec3(0, -150, 1);
		foreach (var _ in Reset(start)) yield return 0;
		var p = P!; var a = p.Position; int dashF = 0; float top = 0;
		cur["dashes_start"] = Dashes(p);
		held.Add(Keys.Up); yield return 0; held.Add(Keys.X);
		for (int i = 1; i <= 60; i++)
		{
			yield return 0; p = P!;
			if (i == 3) held.Remove(Keys.X);
			if (St(p) == "Dashing") { dashF++; top = MathF.Max(top, HSpeed(Vel(p))); }
			else if (dashF > 0) break;
		}
		held.Clear();
		cur["dash_frames"] = dashF; cur["dash_speed"] = R(top); cur["dash_dist"] = R((P!.Position - a).XY().Length());
		cur["dashes_after_ground_dash"] = Dashes(P!);
		foreach (var _ in Wait(30)) yield return 0;
		cur["dashes_after_rest"] = Dashes(P!);
		// air: jump, dash, try a second dash, land
		foreach (var _ in Reset(start)) yield return 0;
		held.Add(Keys.C); foreach (var _ in Wait(12)) yield return 0;
		held.Add(Keys.Up); foreach (var _ in Tap(Keys.X)) yield return 0;
		var air1 = Dashes(P!);
		foreach (var _ in Wait(15)) yield return 0;
		var stBefore = St(P!);
		foreach (var _ in Tap(Keys.X)) yield return 0;
		var st2 = St(P!);
		held.Clear();
		int landF = -1;
		for (int i = 1; i <= 240; i++) { yield return 0; if (Ground(P!)) { landF = i; break; } }
		int refillF = -1;
		for (int i = 0; i <= 60; i++) { if (Dashes(P!) >= 1) { refillF = i; break; } yield return 0; }
		cur["air_dashes_after_dash"] = air1; cur["second_air_dash_state"] = st2 == "Dashing" ? "Dashing" : "no dash";
		cur["state_before_second"] = stBefore; cur["landed"] = landF > 0; cur["refill_frames_after_landing"] = refillF;
	}

	/// climb: grab the arena wall, climb up, wall-kick off.
	IEnumerable<int> P_climb()
	{
		foreach (var _ in Enter(Arena, "", false, "_PROBE")) yield return 0;
		if (P == null) { cur["error"] = "arena did not load"; yield break; }
		foreach (var _ in Reset(new Vec3(0, 190, 1))) yield return 0;
		held.Add(Keys.Up); held.Add(Keys.Z);
		foreach (var _ in Tap(Keys.C, 4)) yield return 0;
		bool grabbed = false;
		for (int i = 0; i < 40 && !grabbed; i++) { yield return 0; grabbed = St(P!) == "Climbing"; }
		cur["grabbed"] = grabbed;
		if (!grabbed) { held.Clear(); yield break; }
		var z0 = P!.Position.Z;
		foreach (var _ in Wait(30)) yield return 0;
		cur["climb_rise_0_5s"] = R(P!.Position.Z - z0);
		cur["still_climbing"] = St(P!) == "Climbing";
		// release Up, keep holding climb: hang
		held.Remove(Keys.Up); foreach (var _ in Wait(20)) yield return 0;
		var zh = P!.Position.Z; foreach (var _ in Wait(20)) yield return 0;
		cur["hang_drift"] = R(P!.Position.Z - zh); cur["hang_state"] = St(P!);
		// wall kick: let go of climb and jump
		held.Remove(Keys.Z); held.Add(Keys.C);
		float away = 0, up = 0;
		for (int i = 1; i <= 12; i++) { yield return 0; away = MathF.Max(away, -Vel(P!).Y); up = MathF.Max(up, Vel(P!).Z); }
		held.Clear();
		cur["kick_away_speed"] = R(away, 1); cur["kick_up_speed"] = R(up, 1);
	}

	/// camera: follows the player at a set distance; the camera stick rotates it.
	IEnumerable<int> P_camera()
	{
		foreach (var _ in Enter(Arena, "", false, "_PROBE")) yield return 0;
		if (P == null) { cur["error"] = "arena did not load"; yield break; }
		foreach (var _ in Reset(new Vec3(0, -150, 1), 60)) yield return 0;
		float Dist() => (W!.Camera.Position - P!.Position).Length();
		float Ang() => MathF.Atan2(W!.Camera.LookAt.Y - W!.Camera.Position.Y, W!.Camera.LookAt.X - W!.Camera.Position.X) * 180 / MathF.PI;
		cur["rest_dist"] = R(Dist(), 1); cur["rest_pitch_dz"] = R(W!.Camera.Position.Z - P!.Position.Z, 1);
		var a0 = Ang();
		held.Add(Keys.A); foreach (var _ in Wait(30)) yield return 0; held.Remove(Keys.A);
		foreach (var _ in Wait(60)) yield return 0;
		var d = Ang() - a0; while (d > 180) d -= 360; while (d < -180) d += 360;
		cur["rotate_0_5s_deg"] = R(d, 0);
		// follow: run 1.5 s, camera stays within its distance band
		foreach (var _ in Reset(new Vec3(0, -150, 1), 60)) yield return 0;
		held.Add(Keys.Up); float maxd = 0;
		for (int i = 0; i < 90; i++) { yield return 0; maxd = MathF.Max(maxd, Dist()); }
		held.Clear(); foreach (var _ in Wait(60)) yield return 0;
		cur["run_max_dist"] = R(maxd, 1); cur["after_run_dist"] = R(Dist(), 1);
	}

	/// interact: in the stock first level, the real strawberry / dash crystal / spring / feather / checkpoint actors.
	IEnumerable<int> P_interact()
	{
		foreach (var _ in Enter("1")) yield return 0;
		if (P == null) { cur["error"] = "map 1 did not load"; yield break; }
		var w = W!;
		// strawberry
		{
			var s = w.All<Strawberry>().Cast<Strawberry>().Where(x => !x.IsLocked && x.BubbleTo == null && !x.IsCollected).OrderBy(x => x.ID, StringComparer.Ordinal).FirstOrDefault();
			var d = new Dictionary<string, object?>();
			if (s != null)
			{
				d["id"] = s.ID;
				var before = Save.CurrentRecord.Strawberries.Count;
				foreach (var _ in Reset(s.Position - Vec3.UnitZ * 3, 1)) yield return 0;
				var states = new List<string>();
				for (int i = 0; i < 600; i++) { var st = St(P!); if (states.Count == 0 || states[^1] != st) states.Add(st); if (states.Contains("StrawbGet") && st == "Normal") break; yield return 0; }
				d["states"] = states.Select(x => (object?)x).ToList();
				d["count_gain"] = Save.CurrentRecord.Strawberries.Count - before;
				d["recorded"] = Save.CurrentRecord.Strawberries.Contains(s.ID);
			}
			cur["strawberry"] = d;
		}
		// dash crystal (single refill)
		{
			var r = w.All<Refill>().Cast<Refill>().Where(x => !x.IsDouble).OrderBy(x => x.Position.X).ThenBy(x => x.Position.Y).FirstOrDefault();
			var d = new Dictionary<string, object?>();
			if (r != null)
			{
				foreach (var _ in Reset(r.Position + Vec3.UnitZ * 20, 1)) yield return 0;
				S(P!, "dashes", 0);
				P!.Position = r.Position - Vec3.UnitZ * 3; P!.Stop();
				foreach (var _ in Wait(3)) yield return 0;
				d["dashes_after"] = Dashes(P!);
				d["cooldown_s"] = R((float)F(r, "tCooldown")!, 1);
			}
			cur["refill"] = d;
		}
		// spring
		{
			var sp = w.All<Spring>().OrderBy(x => x.Position.X).ThenBy(x => x.Position.Y).FirstOrDefault();
			var d = new Dictionary<string, object?>();
			if (sp != null)
			{
				foreach (var _ in Reset(sp.Position + Vec3.UnitZ * 6, 0)) yield return 0;
				float vz = 0; float z0 = P!.Position.Z, zmax = z0;
				for (int i = 0; i < 90; i++) { yield return 0; vz = MathF.Max(vz, Vel(P!).Z); zmax = MathF.Max(zmax, P!.Position.Z); }
				d["launch_speed"] = R(vz); d["rise"] = R(zmax - z0, 0);
			}
			cur["spring"] = d;
		}
		// feather
		{
			var fe = w.All<Feather>().OrderBy(x => x.Position.X).ThenBy(x => x.Position.Y).FirstOrDefault();
			var d = new Dictionary<string, object?>();
			if (fe != null)
			{
				foreach (var _ in Reset(fe.Position - Vec3.UnitZ * 3, 0)) yield return 0;
				var states = new List<string>();
				for (int i = 0; i < 200; i++) { var st = St(P!); if (states.Count == 0 || states[^1] != st) states.Add(st); yield return 0; }
				d["states"] = states.Select(x => (object?)x).ToList();
			}
			cur["feather"] = d;
		}
		// checkpoint: touch the first non-start checkpoint
		{
			var c = w.All<Checkpoint>().Cast<Checkpoint>().Where(x => !x.IsCurrent).OrderBy(x => x.Name, StringComparer.Ordinal).FirstOrDefault();
			var d = new Dictionary<string, object?>();
			if (c != null)
			{
				d["name"] = c.Name;
				foreach (var _ in Reset(c.Position + Vec3.UnitZ * 2, 10)) yield return 0;
				d["entry_checkpoint"] = W!.Entry.CheckPoint; d["saved_checkpoint"] = Save.CurrentRecord.Checkpoint;
			}
			cur["checkpoint"] = d;
		}
	}

	/// hazards: death plane, a death block and a spike block kill; the player respawns at the current checkpoint; deaths count.
	IEnumerable<int> P_hazards()
	{
		Save.Instance.EraseRecord("0_FORSAKEN_CITY");
		foreach (var _ in Enter("1")) yield return 0;
		if (P == null) { cur["error"] = "map 1 did not load"; yield break; }
		var spawn = P!.Position;
		cur["spawn"] = V(spawn);
		foreach (var kind in new[] { "pit", "deathblock", "spikes" })
		{
			var d = new Dictionary<string, object?>();
			var w = W!; var deaths0 = Save.CurrentRecord.Deaths;
			foreach (var _ in Reset(spawn, 10)) yield return 0;
			var p = P!;
			if (kind == "pit") { p.Position = p.Position with { Z = w.DeathPlane - 5 }; }
			else if (kind == "deathblock")
			{
				var b = w.All<DeathBlock>().OrderBy(x => x.Position.X).ThenBy(x => x.Position.Y).FirstOrDefault();
				if (b == null) { d["error"] = "no DeathBlock"; cur[kind] = d; continue; }
				p.Position = b.WorldBounds.Center - Vec3.UnitZ * 3;
			}
			else
			{
				var b = w.All<SpikeBlock>().Cast<SpikeBlock>().OrderBy(x => x.Position.X).ThenBy(x => x.Position.Y).ThenBy(x => x.Position.Z).FirstOrDefault();
				if (b == null) { d["error"] = "no SpikeBlock"; cur[kind] = d; continue; }
				p.Position = b.WorldBounds.Center - Vec3.UnitZ * 3; S(p, "velocity", -b.Direction * 50);
			}
			int deadF = -1;
			for (int i = 0; i < 10; i++) { yield return 0; if (P == null || P.Dead) { deadF = i; break; } }
			d["killed"] = deadF >= 0;
			int respF = -1;
			for (int i = 0; i < 600; i++) { yield return 0; if (W != null && W != w && P != null && !Game.Instance.IsMidTransition) { respF = i; break; } }
			d["respawned"] = respF >= 0;
			d["respawn_s"] = respF >= 0 ? R(respF / 60f, 1) : null;
			if (respF >= 0)
			{
				foreach (var _ in Wait(60)) yield return 0;
				d["entry_reason"] = W!.Entry.Reason.ToString();
				d["respawn_dist_from_spawn"] = R((P!.Position - spawn).Length(), 1);
				d["state_after"] = St(P!);
			}
			d["deaths_gain"] = Save.CurrentRecord.Deaths - deaths0;
			cur[kind] = d;
		}
		// respawn at a later checkpoint
		{
			var w = W!; var c = w.All<Checkpoint>().Cast<Checkpoint>().Where(x => !x.IsCurrent).OrderBy(x => x.Name, StringComparer.Ordinal).FirstOrDefault();
			var d = new Dictionary<string, object?>();
			if (c != null)
			{
				d["name"] = c.Name;
				var cpos = c.Position;
				foreach (var _ in Reset(cpos + Vec3.UnitZ * 2, 10)) yield return 0;
				P!.Position = P!.Position with { Z = w.DeathPlane - 5 };
				for (int i = 0; i < 600; i++) { yield return 0; if (W != null && W != w && P != null && !Game.Instance.IsMidTransition) break; }
				foreach (var _ in Wait(60)) yield return 0;
				d["entry_checkpoint"] = W?.Entry.CheckPoint;
				d["respawn_dist_from_checkpoint"] = P != null ? R((P.Position - cpos).Length(), 1) : null;
			}
			cur["checkpoint_respawn"] = d;
		}
	}

	/// npc: the stock NPCs (Granny, Theo, Badeline, signposts) start their dialog with the talk button and it can be read to the end.
	IEnumerable<int> P_npc()
	{
		foreach (var _ in Enter("1")) yield return 0;
		if (P == null) { cur["error"] = "map 1 did not load"; yield break; }
		var npcs = W!.All<NPC>().Cast<NPC>().OrderBy(n => n.GetType().Name, StringComparer.Ordinal).ThenBy(n => n.Position.X).ThenBy(n => n.Position.Y).ToList();
		var res = new List<object?>();
		var idx = 0;
		foreach (var n0 in npcs)
		{
			var key = n0.GetType().Name + "#" + (idx++);
			var d = new Dictionary<string, object?> { ["npc"] = key };
			// fresh level each time, so earlier talks do not change state
			foreach (var _ in Enter("1")) yield return 0;
			var n = W!.All<NPC>().Cast<NPC>().OrderBy(x => x.GetType().Name, StringComparer.Ordinal).ThenBy(x => x.Position.X).ThenBy(x => x.Position.Y).ElementAt(idx - 1);
			// stand 10 units in front of it (try the 4 sides), on the floor, facing it
			bool over = false;
			foreach (var dir in new[] { -Vec2.UnitY, Vec2.UnitY, -Vec2.UnitX, Vec2.UnitX })
			{
				foreach (var _ in Reset(n.Position + new Vec3(dir * 10, 4), 30)) yield return 0;
				var face = (n.Position - P!.Position).XY().Normalized();
				S(P!, "targetFacing", face); P!.Facing = face;
				yield return 0; yield return 0;
				if (Ground(P!) && n.IsPlayerOver || (Ground(P!) && (P!.Position - n.Position).Length() < n.InteractRadius && MathF.Abs(n.Position.Z - P!.Position.Z) < 2)) { over = true; break; }
			}
			d["reachable"] = over;
			if (!over) { res.Add(d); continue; }
			held.Add(Keys.X); yield return 0; yield return 0; held.Remove(Keys.X);
			bool talking = false; var lines = new List<object?>();
			for (int i = 0; i < 60 * 60; i++)
			{
				yield return 0;
				var cs = W?.Get<Cutscene>();
				if (cs != null)
				{
					talking = true;
					var saying = F(cs, "saying");
					var text = saying != null ? (string?)F(saying, "Text") : null;
					var talkingNow = saying != null && (bool)F(saying, "Talking")!;
					if (!string.IsNullOrEmpty(text) && (lines.Count == 0 || (string?)lines[^1] != text)) lines.Add(text);
					if (i % 20 == 0) { held.Add(Keys.C); } else if (i % 20 == 2) held.Remove(Keys.C);
					if (i == 40 && lines.Count > 0) Shot("npc_" + n0.GetType().Name.ToLower());
				}
				else if (talking && i > 30) break;
				if (talking && lines.Count == 0 && i > 600) break;   // a conversation with no lines (a sign without dialog) stays open
				else if (!talking && i > 120) break;
			}
			held.Clear();
			d["talked"] = talking; d["n_lines"] = lines.Count; d["first_line"] = lines.Count > 0 ? lines[0] : null;
			d["ended"] = W?.Get<Cutscene>() == null; d["state_after"] = P != null ? St(P) : null;
			res.Add(d);
		}
		cur["npcs"] = res;
	}

	/// cassette: touching a cassette in the first level enters its sub-level.
	IEnumerable<int> P_cassette()
	{
		Save.Instance.EraseRecord("0_FORSAKEN_CITY");
		foreach (var _ in Enter("1")) yield return 0;
		if (P == null) { cur["error"] = "map 1 did not load"; yield break; }
		var cas = W!.All<Cassette>().Cast<Cassette>().Where(c => !string.IsNullOrEmpty(c.Map)).OrderBy(c => c.Map, StringComparer.Ordinal).ToList();
		cur["cassette_maps"] = cas.Select(c => (object?)c.Map).ToList();
		var first = cas.FirstOrDefault();
		if (first == null) yield break;
		var w = W!;
		foreach (var _ in Reset(first.Position - Vec3.UnitZ * 3, 0)) yield return 0;
		var states = new List<string>();
		for (int i = 0; i < 900; i++)
		{
			// hold the player on the cassette (it ignores touches for its first second)
			if (i < 120 && W == w && P != null && St(P) == "Normal") { P.Position = first.Position - Vec3.UnitZ * 3; P.Stop(); }
			yield return 0;
			if (P != null) { var st = St(P); if (states.Count == 0 || states[^1] != st) states.Add(st); }
			if (W != null && W != w && P != null && !Game.Instance.IsMidTransition) break;
		}
		foreach (var _ in Wait(30)) yield return 0;
		cur["states"] = states.Select(x => (object?)x).ToList();
		cur["entered_map"] = W != w ? W?.Entry.Map : null; cur["submap"] = W?.Entry.Submap;
		Shot("cassette_submap");
	}

	/// save: collect a strawberry + checkpoint, then Pause -> Save & Quit with the keyboard; the save file holds the progress.
	IEnumerable<int> P_save()
	{
		Save.Instance.EraseRecord("0_FORSAKEN_CITY");
		foreach (var _ in Enter("1")) yield return 0;
		if (P == null) { cur["error"] = "map 1 did not load"; yield break; }
		var w = W!;
		var s = w.All<Strawberry>().Cast<Strawberry>().Where(x => !x.IsLocked && x.BubbleTo == null).OrderBy(x => x.ID, StringComparer.Ordinal).First();
		foreach (var _ in Reset(s.Position - Vec3.UnitZ * 3, 1)) yield return 0;
		foreach (var _ in WaitUntil(() => Save.CurrentRecord.Strawberries.Contains(s.ID) && St(P!) == "Normal", 600)) yield return 0;
		var c = w.All<Checkpoint>().Cast<Checkpoint>().Where(x => !x.IsCurrent).OrderBy(x => x.Name, StringComparer.Ordinal).First();
		foreach (var _ in Reset(c.Position + Vec3.UnitZ * 2, 30)) yield return 0;
		var path = Path.Join(App.UserPath, Save.FileName);
		var mt0 = File.Exists(path) ? File.GetLastWriteTimeUtc(path) : DateTime.MinValue;
		// pause menu: Resume, Retry, Options, Save & Quit
		foreach (var _ in Tap(Keys.Escape)) yield return 0;
		foreach (var _ in Wait(20)) yield return 0;
		cur["paused"] = W?.Paused;
		Shot("pause_menu");
		for (int i = 0; i < 3; i++) { foreach (var _ in Tap(Keys.Down)) yield return 0; foreach (var _ in Wait(8)) yield return 0; }
		foreach (var _ in Tap(Keys.C)) yield return 0;
		foreach (var _ in WaitUntil(() => Top is Overworld && !Game.Instance.IsMidTransition, 600)) yield return 0;
		cur["back_to"] = Top?.GetType().Name;
		cur["file_written"] = File.Exists(path) && File.GetLastWriteTimeUtc(path) > mt0;
		var loaded = File.Exists(path) ? Save.Deserialize(File.ReadAllText(path)) : null;
		var rec = loaded?.TryGetRecord("0_FORSAKEN_CITY");
		cur["file_level"] = loaded?.LevelID;
		cur["file_strawberries"] = rec?.Strawberries.OrderBy(x => x, StringComparer.Ordinal).Select(x => (object?)x).ToList();
		cur["file_checkpoint"] = rec?.Checkpoint;
		cur["expected_strawberry"] = s.ID; cur["expected_checkpoint"] = c.Name;
	}

	/// saveload: what a fresh start of the game read from the save file (run after `save` in the same user dir).
	IEnumerable<int> P_saveload()
	{
		var rec = Save.Instance.TryGetRecord("0_FORSAKEN_CITY");
		cur["level"] = Save.Instance.LevelID;
		cur["strawberries"] = rec?.Strawberries.OrderBy(x => x, StringComparer.Ordinal).Select(x => (object?)x).ToList();
		cur["checkpoint"] = rec?.Checkpoint;
		// and the level resumes at that checkpoint
		if (rec != null)
		{
			var lv = Assets.Levels.FirstOrDefault(l => l.ID == "0_FORSAKEN_CITY");
			if (lv != null)
			{
				lv.Enter(new SlideWipe());
				foreach (var _ in WaitUntil(() => W != null && P != null && !Game.Instance.IsMidTransition, 900)) yield return 0;
				foreach (var _ in Wait(20)) yield return 0;
				cur["resume_checkpoint"] = W?.Entry.CheckPoint;
				var c = W?.All<Checkpoint>().Cast<Checkpoint>().FirstOrDefault(x => x.Name == rec.Checkpoint);
				cur["resume_dist"] = c != null && P != null ? R((P.Position - c.Position).Length(), 1) : null;
				cur["strawberry_hidden"] = W?.All<Strawberry>().Cast<Strawberry>().Where(x => rec.Strawberries.Contains(x.ID)).All(x => x.IsCollected);
			}
		}
	}

	/// perf: update / render CPU time per frame in the first level, camera turning (World's own debug stopwatches).
	IEnumerable<int> P_perf()
	{
		foreach (var _ in Enter("1")) yield return 0;
		if (P == null) { cur["error"] = "map 1 did not load"; yield break; }
		foreach (var _ in Wait(60)) yield return 0;
		var w = W!;
		var upd = (System.Diagnostics.Stopwatch)F(w, "debugUpdTimer")!;
		var rnd = (System.Diagnostics.Stopwatch)F(w, "debugRndTimer")!;
		var u = new List<double>(); var r = new List<double>();
		var wall = System.Diagnostics.Stopwatch.StartNew(); int f0 = frame;
		held.Add(Keys.A);
		for (int i = 0; i < 600; i++) { yield return 0; u.Add(upd.Elapsed.TotalMilliseconds); r.Add(((TimeSpan)F(w, "lastDebugRndTime")!).TotalMilliseconds); }
		held.Clear();
		u.Sort(); r.Sort();
		cur["update_ms_median"] = Math.Round(u[u.Count / 2], 3); cur["render_ms_median"] = Math.Round(r[r.Count / 2], 3);
		cur["update_ms_p95"] = Math.Round(u[(int)(u.Count * .95)], 3); cur["render_ms_p95"] = Math.Round(r[(int)(r.Count * .95)], 3);
		cur["fps"] = Math.Round((frame - f0) / wall.Elapsed.TotalSeconds, 1);
	}

	// ------------------------------------------------------------ state server (gab) ------------------------------------------------------------
	string stateDir = "";
	static bool textsOn;
	static readonly Dictionary<string, long> texts = new();
	static readonly System.Diagnostics.Stopwatch clock = System.Diagnostics.Stopwatch.StartNew();
	readonly HashSet<string> vTypes = new(), vMaps = new(), vLevels = new(), vFlags = new();

	/// called from UI.Text (hook line added by apply_bridge.sh): remembers what text was drawn when
	public static void Seen(string text)
	{
		if (!textsOn || string.IsNullOrEmpty(text)) return;
		texts[text.Length > 200 ? text[..200] : text] = clock.ElapsedMilliseconds;
		if (texts.Count > 400) foreach (var k in texts.Where(kv => kv.Value < clock.ElapsedMilliseconds - 1000).Select(kv => kv.Key).ToList()) texts.Remove(k);
	}

	void LoadVanilla()
	{
		var f = Environment.GetEnvironmentVariable("C64_VANILLA") ?? "";
		if (f == "" || !File.Exists(f)) return;
		foreach (var l in File.ReadAllLines(f))
		{
			var p = l.Split(' ', 2); if (p.Length < 2) continue;
			(p[0] switch { "type" => vTypes, "map" => vMaps, "level" => vLevels, "flag" => vFlags, _ => null })?.Add(p[1].Trim());
		}
	}

	void ServeState()
	{
		var req = Path.Join(stateDir, "req");
		if (!File.Exists(req)) return;
		string json;
		try { json = Json(State()); } catch (Exception e) { json = Json(new Dictionary<string, object?> { ["error"] = e.GetType().Name + ": " + e.Message }); }
		var tmp = Path.Join(stateDir, "state.tmp");
		File.WriteAllText(tmp, json); File.Move(tmp, Path.Join(stateDir, "state.json"), true);
		try { File.Delete(req); } catch { }
	}

	bool VanillaType(Type t) { while (t.DeclaringType != null) t = t.DeclaringType; return t == typeof(C64Probe) || vTypes.Contains(t.Name) || t.Name.Contains('<'); }
	static Dictionary<string, object?> Vd(Vec3 v) => new() { ["x"] = R(v.X, 1), ["y"] = R(v.Y, 1), ["z"] = R(v.Z, 1) };   // keyed: state_diff per axis
	static object? Val(object? v) => v switch { null => null, bool or int or long or float or double or string => v is string s && s.Length > 200 ? s[..200] : v,
		Enum e => e.ToString(), Vec3 x => Vd(x), Vec2 y => new[] { R(y.X), R(y.Y) }, _ => v.GetType().IsPrimitive ? Convert.ToDouble(v, CultureInfo.InvariantCulture) : null };

	static Dictionary<string, object?>? MenuInfo(Menu? m)
	{
		if (m == null) return null;
		var cur = (Menu)F(m, "CurrentMenu")!;
		var items = ((System.Collections.IEnumerable)F(cur, "items")!).Cast<object>().Select(i => (object?)((Menu.Item)i).Label).ToList();
		return new() { ["title"] = cur.Title, ["items"] = items, ["index"] = cur.Index };
	}

	Dictionary<string, object?> State()
	{
		var top = Top; var w = W; var p = P; var now = clock.ElapsedMilliseconds;
		var e = new Dictionary<string, object?>();
		e["screen"] = top?.GetType().Name; e["transition"] = Game.Instance.IsMidTransition; e["paused"] = w?.Paused;
		e["level_id"] = Save.Instance.LevelID; e["map"] = w?.Entry.Map; e["checkpoint"] = w?.Entry.CheckPoint; e["submap"] = w?.Entry.Submap;
		e["world_time_s"] = w != null ? R(w.GeneralTimer, 1) : null;
		e["player"] = p == null ? null : new Dictionary<string, object?> { ["pos"] = Vd(p.Position), ["vel"] = Vd(p.Velocity), ["state"] = St(p),
			["on_ground"] = Ground(p), ["dashes"] = p.Dashes, ["dead"] = p.Dead, ["facing"] = new Dictionary<string, object?> { ["x"] = R(p.Facing.X), ["y"] = R(p.Facing.Y) } };
		var rec = Save.Instance.TryGetRecord(Save.Instance.LevelID);
		e["record"] = rec == null ? null : new Dictionary<string, object?> { ["strawberries"] = rec.Strawberries.Count,
			["strawberry_ids"] = rec.Strawberries.OrderBy(x => x, StringComparer.Ordinal).Take(30).Select(x => (object?)x).ToList(), ["deaths"] = rec.Deaths,
			["time_s"] = R((float)rec.Time.TotalSeconds, 1), ["checkpoint"] = rec.Checkpoint,
			["completed_submaps"] = rec.CompletedSubMaps.OrderBy(x => x, StringComparer.Ordinal).Select(x => (object?)x).ToList(),
			["flags"] = rec.Flags.Where(kv => vFlags.Contains(kv.Key)).OrderBy(kv => kv.Key).ToDictionary(kv => kv.Key, kv => (object?)kv.Value) };
		e["strawberries_total"] = Save.Instance.Records.Sum(r => r.Strawberries.Count);
		e["actors"] = w == null ? null : Counts(w).Where(kv => vTypes.Contains(kv.Key)).ToDictionary(kv => kv.Key, kv => kv.Value);
		e["menu"] = w != null ? (w.Paused ? MenuInfo((Menu)F(w, "pauseMenu")!) : null) : top is Overworld ow ? OverworldInfo(ow) : null;
		// mods: what exists in the engine that the vanilla game does not have
		var m = new Dictionary<string, object?>();
		if (w != null)
		{
			var mine = w.Actors.Where(a => !VanillaType(a.GetType())).ToList();
			m["actor_counts"] = mine.GroupBy(a => a.GetType().Name).OrderBy(g => g.Key).ToDictionary(g => g.Key, g => (object?)g.Count());
			var at = p?.Position ?? Vec3.Zero;
			m["actors"] = mine.OrderBy(a => (a.Position - at).Length()).Take(20).Select(a =>
			{
				var d = new Dictionary<string, object?> { ["type"] = a.GetType().Name, ["pos"] = Vd(a.Position), ["dist"] = R((a.Position - at).Length(), 1) };
				foreach (var f in a.GetType().GetFields(BindingFlags.Public | BindingFlags.Instance | BindingFlags.DeclaredOnly).Take(8)) d[f.Name] = Val(f.GetValue(a));
				foreach (var pr in a.GetType().GetProperties(BindingFlags.Public | BindingFlags.Instance | BindingFlags.DeclaredOnly).Where(x => x.GetIndexParameters().Length == 0).Take(8))
					try { d[pr.Name] = Val(pr.GetValue(a)); } catch { }
				return (object?)d;
			}).ToList();
			if (!vMaps.Contains(w.Entry.Map)) m["map"] = w.Entry.Map;
		}
		m["flags"] = Save.Instance.Records.SelectMany(r => r.Flags.Where(kv => !vFlags.Contains(kv.Key)).Select(kv => (r.ID + ":" + kv.Key, kv.Value)))
			.Take(30).ToDictionary(x => x.Item1, x => (object?)x.Value);
		var st = new Dictionary<string, object?>();
		foreach (var t in typeof(Game).Assembly.GetTypes().Where(t => !VanillaType(t)))
			foreach (var f in t.GetFields(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static | BindingFlags.DeclaredOnly).Where(f => !f.Name.Contains('<')))
			{
				if (st.Count >= 30) break;
				try { var v = Val(f.GetValue(null)); if (v != null) st[t.Name + "." + f.Name] = v; } catch { }
			}
		m["statics"] = st;
		m["maps"] = Assets.Maps.Keys.Where(k => !vMaps.Contains(k) && k != Arena).OrderBy(k => k, StringComparer.Ordinal).Take(30).Select(k => (object?)k).ToList();
		m["levels"] = Assets.Levels.Where(l => !vLevels.Contains(l.ID)).Take(30).ToDictionary(l => l.ID, l => (object?)l.Name);
		// on screen: UI.Text of the last 300 ms (HUD, menus, cards, prompts) + the visible part of the dialog line
		var sc = texts.Where(kv => kv.Value >= now - 300).OrderBy(kv => kv.Value).Select(kv => kv.Key).Distinct().TakeLast(29).ToList();
		var cs = w?.Get<Cutscene>();
		if (cs != null)
		{
			var say = F(cs, "saying");
			var txt = say != null ? (string?)F(say, "Text") : null; var n = say != null ? (int)F(say, "Characters")! : 0;
			if (!string.IsNullOrEmpty(txt)) sc.Add("dialog: " + txt[..Math.Clamp(n, 0, Math.Min(txt.Length, 200))]);
		}
		return new() { ["engine"] = e, ["mod"] = m, ["screen_text"] = sc.Select(x => (object?)x).ToList() };
	}

	static Dictionary<string, object?> OverworldInfo(Overworld ow)
	{
		var entries = ((System.Collections.IList)F(ow, "entries")!).Cast<Overworld.Entry>().ToList();
		var i = (int)F(ow, "index")!;
		return new() { ["state"] = F(ow, "state")?.ToString(), ["selected"] = i >= 0 && i < entries.Count ? entries[i].Level.ID : null,
			["levels"] = entries.Select(x => (object?)x.Level.ID).ToList() };
	}

	// ------------------------------------------------------------ tiny JSON writer ------------------------------------------------------------
	static string Json(object? o)
	{
		var sb = new StringBuilder(); Write(sb, o); return sb.ToString();
	}
	static void Write(StringBuilder sb, object? o)
	{
		switch (o)
		{
			case null: sb.Append("null"); break;
			case bool b: sb.Append(b ? "true" : "false"); break;
			case string s:
				sb.Append('"');
				foreach (var ch in s)
					sb.Append(ch switch { '"' => "\\\"", '\\' => "\\\\", '\n' => "\\n", '\r' => "\\r", '\t' => "\\t", _ => ch < ' ' ? $"\\u{(int)ch:x4}" : ch.ToString() });
				sb.Append('"'); break;
			case float f: sb.Append(float.IsFinite(f) ? f.ToString("R", CultureInfo.InvariantCulture) : "null"); break;
			case double d: sb.Append(double.IsFinite(d) ? d.ToString("R", CultureInfo.InvariantCulture) : "null"); break;
			case int or long: sb.Append(Convert.ToString(o, CultureInfo.InvariantCulture)); break;
			case System.Collections.IDictionary dict:
				sb.Append('{'); bool first = true;
				foreach (System.Collections.DictionaryEntry kv in dict) { if (!first) sb.Append(','); first = false; Write(sb, kv.Key.ToString()); sb.Append(':'); Write(sb, kv.Value); }
				sb.Append('}'); break;
			case System.Collections.IEnumerable list:
				sb.Append('['); bool f1 = true;
				foreach (var x in list) { if (!f1) sb.Append(','); f1 = false; Write(sb, x); }
				sb.Append(']'); break;
			default: Write(sb, o.ToString()); break;
		}
	}
}
