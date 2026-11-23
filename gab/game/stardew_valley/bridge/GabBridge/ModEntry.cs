// GAB Bridge: the GameAnything-Bench harness mod for Stardew Valley 1.6 (SMAPI 4).
//
// Channel: request / answer files. The adapter writes <GAB_BRIDGE_DIR>/req/<id>.json ({"cmd": ..., ...}); the bridge reads them on
// the game thread (every 3rd tick, also at the title screen and while loading) and writes <GAB_BRIDGE_DIR>/ans/<id>.json.
// Config: <GAB_BRIDGE_DIR>/bridge.json  {"autoload": save folder | null, "newgame": {...} | null, "vanilla": path of vanilla.json}.
//
// It changes no game behaviour: it only reads the game (state, text drawn on screen through prefix-only Harmony hooks that copy the
// string) and, for the evaluator's probes, runs setup steps on request (warp the farmer, move a villager next to them, set the
// clock, place / clear a tile). A player never sends requests; the agent's dev kit only uses `state`.
using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Reflection.Metadata;
using System.Reflection.Metadata.Ecma335;
using System.Reflection.PortableExecutable;
using System.Text;
using System.Text.RegularExpressions;
using HarmonyLib;
using Microsoft.Xna.Framework;
using Microsoft.Xna.Framework.Graphics;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using StardewModdingAPI;
using StardewModdingAPI.Events;
using StardewValley;
using StardewValley.BellsAndWhistles;
using StardewValley.Characters;
using StardewValley.Menus;
using StardewValley.Mods;
using StardewValley.Monsters;
using StardewValley.Objects;
using StardewValley.TerrainFeatures;
using SObject = StardewValley.Object;

namespace GabBridge
{
    public class ModEntry : Mod
    {
        // SMAPI's bundled mods, the harness and Content Patcher (installed in every instance as the framework for content packs)
        private static readonly string[] HarnessMods = { "SMAPI.ConsoleCommands", "SMAPI.SaveBackup", "gab.GabBridge", "Pathoschild.ContentPatcher" };
        private string Dir, ReqDir, AnsDir;
        private JObject Cfg = new JObject();
        private Vanilla V = new Vanilla();
        private int titleTicks, ticks;
        private bool autoDone;
        private readonly Stopwatch Watch = Stopwatch.StartNew();
        private readonly List<long[]> ClockLog = new List<long[]>();      // [game time, ms since start]
        private readonly List<object> WarpLog = new List<object>();        // the farmer's location changes (arrival tile)
        private HashSet<string> StartFlags = new HashSet<string>();
        private readonly Dictionary<string, Dictionary<string, string>> DataNow = new Dictionary<string, Dictionary<string, string>>();
        private readonly HashSet<string> Dirty = new HashSet<string>();
        private bool AllDirty = true;
        private readonly Dictionary<string, bool> VocabCache = new Dictionary<string, bool>();

        // text drawn on screen: the strings of the frame being drawn and of the last complete frame
        internal static HashSet<string> FrameText = new HashSet<string>();
        internal static List<string> LastFrame = new List<string>();
        internal static readonly List<string> FrameOrder = new List<string>();

        public override void Entry(IModHelper helper)
        {
            this.Dir = Environment.GetEnvironmentVariable("GAB_BRIDGE_DIR");
            if (string.IsNullOrEmpty(this.Dir)) { this.Monitor.Log("GAB_BRIDGE_DIR not set: bridge idle", LogLevel.Info); return; }
            this.ReqDir = Path.Combine(this.Dir, "req"); this.AnsDir = Path.Combine(this.Dir, "ans");
            Directory.CreateDirectory(this.ReqDir); Directory.CreateDirectory(this.AnsDir);
            try { this.Cfg = JObject.Parse(File.ReadAllText(Path.Combine(this.Dir, "bridge.json"))); } catch (Exception) { }
            string vp = (string)this.Cfg["vanilla"];
            if (!string.IsNullOrEmpty(vp) && File.Exists(vp))
            {
                try { this.V = Vanilla.Load(vp); } catch (Exception e) { this.Monitor.Log("vanilla.json: " + e.Message, LogLevel.Warn); }
            }
            helper.Events.GameLoop.UpdateTicked += this.OnTick;
            helper.Events.GameLoop.SaveLoaded += (s, e) => this.OnLoaded();
            // the data snapshot is (re)computed here, on the game thread at the start of a day (also right after a load), never
            // inside a state request: state() stays well under a second
            helper.Events.GameLoop.DayStarted += (s, e) =>
            {
                this.AllDirty = true;
                try { this.RefreshData(); } catch (Exception ex) { this.Monitor.Log("data: " + ex.Message, LogLevel.Trace); }
            };
            helper.Events.GameLoop.TimeChanged += (s, e) =>
            {
                this.ClockLog.Add(new long[] { e.NewTime, this.Watch.ElapsedMilliseconds });
                if (this.ClockLog.Count > 60) this.ClockLog.RemoveAt(0);
            };
            helper.Events.Player.Warped += (s, e) =>
            {
                if (!e.IsLocalPlayer) return;
                this.WarpLog.Add(new { from = e.OldLocation?.NameOrUniqueName, to = e.NewLocation?.NameOrUniqueName, x = e.Player.TilePoint.X, y = e.Player.TilePoint.Y, ms = this.Watch.ElapsedMilliseconds });
                if (this.WarpLog.Count > 20) this.WarpLog.RemoveAt(0);
            };
            helper.Events.Content.AssetsInvalidated += (s, e) =>
            {
                foreach (var n in e.NamesWithoutLocale) this.Dirty.Add(Norm(n.Name));
            };
            helper.Events.Display.Rendering += (s, e) => SwapFrame();
            try { this.PatchText(); } catch (Exception e) { this.Monitor.Log("text hooks: " + e.Message, LogLevel.Warn); }
            this.Monitor.Log("GAB bridge ready: " + this.Dir, LogLevel.Info);
        }

        // ------------------------------------------------------------------ text capture (prefix only, copies the string)
        private void PatchText()
        {
            var h = new Harmony(this.ModManifest.UniqueID);
            var pre = new HarmonyMethod(typeof(ModEntry).GetMethod(nameof(TextPrefix), BindingFlags.Static | BindingFlags.NonPublic));
            int n = 0;
            foreach (var m in typeof(SpriteBatch).GetMethods(BindingFlags.Public | BindingFlags.Instance).Where(m => m.Name == "DrawString"))
            { h.Patch(m, prefix: pre); n++; }
            foreach (var m in typeof(SpriteText).GetMethods(BindingFlags.Public | BindingFlags.Static).Where(m => m.Name == "drawString"))
            { h.Patch(m, prefix: pre); n++; }
            this.Monitor.Log($"text hooks on {n} methods", LogLevel.Trace);
        }

        private static void TextPrefix(object[] __args)
        {
            try
            {
                foreach (var a in __args)
                {
                    string s = a as string ?? (a as StringBuilder)?.ToString();
                    if (s == null) continue;
                    s = s.Replace("\n", " ").Replace("^", " ").Trim();
                    if (s.Length >= 2 && FrameText.Count < 400 && FrameText.Add(s)) FrameOrder.Add(s);
                    return;
                }
            }
            catch (Exception) { }
        }

        private static void SwapFrame()
        {
            LastFrame = new List<string>(FrameOrder);
            FrameOrder.Clear(); FrameText.Clear();
        }

        // ------------------------------------------------------------------ requests
        private void OnTick(object sender, UpdateTickedEventArgs e)
        {
            this.ticks++;
            if (Game1.activeClickableMenu is TitleMenu) this.titleTicks++; else this.titleTicks = 0;
            if (!this.autoDone && this.titleTicks > 90) this.AutoStart();
            if (this.ticks % 3 != 0) return;
            string[] files;
            try { files = Directory.GetFiles(this.ReqDir, "*.json"); } catch (Exception) { return; }
            Array.Sort(files, StringComparer.Ordinal);
            foreach (var f in files)
            {
                string id = Path.GetFileNameWithoutExtension(f);
                object ans;
                try
                {
                    var req = JObject.Parse(File.ReadAllText(f));
                    File.Delete(f);
                    ans = this.Handle(req);
                }
                catch (Exception ex)
                {
                    try { File.Delete(f); } catch (Exception) { }
                    ans = new { error = ex.GetType().Name + ": " + ex.Message, at = ex.StackTrace?.Split('\n').FirstOrDefault()?.Trim() };
                }
                try
                {
                    string tmp = Path.Combine(this.AnsDir, id + ".tmp");
                    File.WriteAllText(tmp, JsonConvert.SerializeObject(ans, JsonSet));
                    File.Move(tmp, Path.Combine(this.AnsDir, id + ".json"), true);
                }
                catch (Exception ex) { this.Monitor.Log("answer " + id + ": " + ex.Message, LogLevel.Warn); }
            }
        }

        private static readonly JsonSerializerSettings JsonSet = new JsonSerializerSettings
        { ReferenceLoopHandling = ReferenceLoopHandling.Ignore, NullValueHandling = NullValueHandling.Include };

        private object Handle(JObject r)
        {
            string cmd = (string)r["cmd"];
            switch (cmd)
            {
                case "ping": return new { ok = true, screen = Screen(), ticks = this.ticks, menu = Game1.activeClickableMenu?.GetType().Name, location = Game1.currentLocation?.Name, time = Game1.timeOfDay, day = Game1.Date?.TotalDays };
                case "state": return this.State();
                case "load": return this.LoadSave((string)r["save"]);
                case "exit_title": Game1.ExitToTitle(); this.autoDone = true; return new { ok = true };
                case "newgame": return this.NewGame((JObject)r["game"] ?? new JObject());
                case "dump_vanilla": return this.DumpVanilla((string)r["path"]);
                case "warp":
                    {
                        if (!Context.IsWorldReady) return new { ok = false, error = "not in game" };
                        Game1.exitActiveMenu();
                        Game1.warpFarmer((string)r["location"], (int)r["x"], (int)r["y"], (int?)r["facing"] ?? 2);
                        return new { ok = true };
                    }
                case "face": Game1.player.faceDirection((int)r["dir"]); return new { ok = true, facing = Game1.player.FacingDirection };
                case "settime":
                    {
                        Game1.timeOfDay = (int)r["time"]; Game1.gameTimeInterval = 0;
                        return new { ok = true, time = Game1.timeOfDay };
                    }
                case "place": return this.Place((string)r["id"], (int)r["x"], (int)r["y"]);
                case "clear": return this.Clear((int)r["x1"], (int)r["y1"], (int)r["x2"], (int)r["y2"]);
                case "tile": return this.TileInfo((int)r["x"], (int)r["y"]);
                case "npc": return this.NpcTo((string)r["name"], (int)r["x"], (int)r["y"], (int?)r["facing"] ?? 2);
                case "warps":
                    return new { location = Game1.currentLocation?.Name, warps = Game1.currentLocation?.warps.Select(w => new { x = w.X, y = w.Y, to = w.TargetName, tx = w.TargetX, ty = w.TargetY }).ToList() };
                case "menu": return this.MenuInfo();
                case "close_menu":
                    {
                        if (Game1.activeClickableMenu != null) Game1.exitActiveMenu();
                        Game1.dialogueUp = false;
                        return new { ok = true, menu = Game1.activeClickableMenu?.GetType().Name };
                    }
                case "skip_event": return this.SkipEvent();
                case "clock":
                    return new { now_ms = this.Watch.ElapsedMilliseconds, time = Game1.timeOfDay, interval = Game1.gameTimeInterval, paused = Game1.paused, should_pass = Game1.shouldTimePass(), menu = Game1.activeClickableMenu?.GetType().Name, log = this.ClockLog.ToList() };
                case "warplog": return new { now_ms = this.Watch.ElapsedMilliseconds, log = this.WarpLog.ToList() };
                case "screen_of":
                    {
                        // where a character is drawn on the screen (game pixels = screen pixels at zoom 1): for a real click on it
                        var c = Game1.currentLocation?.characters.FirstOrDefault(n => n.Name == (string)r["name"]);
                        if (c == null) return new { ok = false, error = "not here: " + (string)r["name"] };
                        var bb = c.GetBoundingBox();
                        var v = Game1.GlobalToLocal(Game1.viewport, new Vector2(bb.Center.X, bb.Center.Y - 40));
                        float z = Game1.options.zoomLevel;
                        return new { ok = true, x = (int)(v.X * z), y = (int)(v.Y * z) };
                    }
                case "flags": return new { flags = Flags().OrderBy(x => x).ToList() };
                case "inv":
                    return new { current = Game1.player?.CurrentToolIndex, items = Game1.player?.Items.Select((it, i) => new { slot = i, name = it?.Name, display = it?.DisplayName, id = it?.QualifiedItemId, stack = it?.Stack ?? 0 }).Where(x => x.id != null).ToList() };
                case "bed":
                    {
                        var home = Utility.getHomeOfFarmer(Game1.player);
                        var b = home.GetPlayerBedSpot();
                        return new { location = home.NameOrUniqueName, x = b.X, y = b.Y, in_bed = Game1.player.isInBed.Value };
                    }
                default: return new { error = "unknown cmd " + cmd };
            }
        }

        // ------------------------------------------------------------------ start: autoload / new game
        private void AutoStart()
        {
            if (Game1.activeClickableMenu is not TitleMenu tm) return;
            this.autoDone = true;
            try { tm.skipToTitleButtons(); } catch (Exception) { }
            if (this.Cfg["newgame"] is JObject ng) { this.NewGame(ng); return; }
            string save = (string)this.Cfg["autoload"];
            if (!string.IsNullOrEmpty(save)) this.LoadSave(save);
        }

        private object LoadSave(string save)
        {
            if (Game1.activeClickableMenu is not TitleMenu) return new { ok = false, error = "not at the title screen" };
            string path = Path.Combine(Constants.SavesPath, save);
            if (!Directory.Exists(path)) return new { ok = false, error = "no save " + path };
            Game1.multiplayerMode = 0;
            SaveGame.Load(save);
            Game1.exitActiveMenu();
            this.Monitor.Log("GAB bridge: loading save " + save, LogLevel.Info);
            return new { ok = true, loading = save };
        }

        private object NewGame(JObject g)
        {
            if (Game1.activeClickableMenu is not TitleMenu tm) return new { ok = false, error = "not at the title screen" };
            Game1.player.Name = (string)g["name"] ?? "Farmer";
            Game1.player.displayName = Game1.player.Name;
            Game1.player.farmName.Value = (string)g["farm"] ?? "Bench";
            Game1.player.favoriteThing.Value = (string)g["favorite"] ?? "Games";
            Game1.player.isCustomized.Value = true;
            Game1.player.ConvertClothingOverrideToClothesItems();
            Game1.whichFarm = 0;
            Game1.multiplayerMode = 0;
            if (g["seed"] != null) Game1.startingGameSeed = (ulong)g["seed"];
            tm.createdNewCharacter(true);        // the title menu's own "skip intro" path: farmhouse, spring 1, 6:00; saves on the new day
            this.Monitor.Log("GAB bridge: new game created (skip intro)", LogLevel.Info);
            return new { ok = true };
        }

        private void OnLoaded()
        {
            this.StartFlags = new HashSet<string>(Flags());
            this.AllDirty = true; this.DataNow.Clear();          // DayStarted follows and computes the data snapshot
        }

        // ------------------------------------------------------------------ state (docs/STATE_SPEC.md)
        private static string Screen()
        {
            if (Game1.gameMode == 6) return "loading";
            if (Game1.activeClickableMenu is TitleMenu || Game1.gameMode == 0) return "title";
            if (!Context.IsWorldReady) return "loading";
            if (Game1.activeClickableMenu is SaveGameMenu || Game1.activeClickableMenu is ShippingMenu || Game1.newDay) return "day end";
            if (Game1.currentMinigame != null) return "minigame";
            if (Game1.eventUp && Game1.CurrentEvent != null) return "event";
            if (Game1.activeClickableMenu is DialogueBox) return "dialogue";
            if (Game1.activeClickableMenu != null) return "menu";
            return "in game";
        }

        private static readonly string[] Dirs = { "up", "right", "down", "left" };

        private object State()
        {
            var o = new Dictionary<string, object> { ["running"] = true };
            o["engine"] = this.Engine();
            o["mod"] = this.ModPart();
            o["screen_text"] = this.ScreenText();
            return o;
        }

        private Dictionary<string, object> Engine()
        {
            var E = new Dictionary<string, object>();
            string[] keys = { "screen", "menu", "location", "location_name", "tile", "facing", "health", "max_health", "stamina", "max_stamina", "money",
                              "date", "time", "weather", "inventory", "held", "skills", "riding", "event", "paused", "player", "farm", "quests",
                              "friendship", "npcs_here", "flags_new", "mailbox" };
            foreach (var k in keys) E[k] = null;
            E["screen"] = Screen();
            E["menu"] = Game1.activeClickableMenu?.GetType().Name;
            if (!Context.IsWorldReady || Game1.player == null || Game1.currentLocation == null) return E;
            var p = Game1.player; var loc = Game1.currentLocation;
            E["location"] = loc.NameOrUniqueName;
            E["location_name"] = Cut(loc.DisplayName, 80);
            E["tile"] = new { x = p.TilePoint.X, y = p.TilePoint.Y };
            E["facing"] = Dirs[Math.Clamp(p.FacingDirection, 0, 3)];
            E["health"] = p.health; E["max_health"] = p.maxHealth;
            E["stamina"] = (int)Math.Round(p.Stamina); E["max_stamina"] = p.MaxStamina;
            E["money"] = p.Money;
            E["date"] = new { year = Game1.year, season = Game1.currentSeason, day = Game1.dayOfMonth, weekday = Game1.shortDayNameFromDayOfSeason(Game1.dayOfMonth) };
            E["time"] = Game1.timeOfDay;
            try { E["weather"] = loc.GetWeather()?.Weather ?? (Game1.isRaining ? "Rain" : "Sun"); } catch (Exception) { E["weather"] = Game1.isRaining ? "Rain" : "Sun"; }
            var inv = new SortedDictionary<string, int>();
            foreach (var it in p.Items) if (it != null) { string n = Cut(it.DisplayName, 60); inv[n] = (inv.TryGetValue(n, out int c) ? c : 0) + it.Stack; }
            E["inventory"] = inv;
            E["held"] = p.CurrentItem?.DisplayName;
            E["skills"] = new { farming = p.FarmingLevel, mining = p.MiningLevel, foraging = p.ForagingLevel, fishing = p.FishingLevel, combat = p.CombatLevel };
            E["riding"] = p.mount?.Name;
            E["event"] = Game1.eventUp ? Game1.CurrentEvent?.id : null;
            E["paused"] = Game1.paused || !Game1.shouldTimePass();
            E["player"] = Cut(p.Name, 40); E["farm"] = Cut(p.farmName.Value, 40);
            E["quests"] = p.questLog.Select(q => Cut(SafeQuestName(q), 80)).Take(15).ToList();
            var fr = new SortedDictionary<string, int>();
            foreach (var kv in p.friendshipData.Pairs) if (kv.Value.Points != 0) fr[kv.Key] = kv.Value.Points;
            E["friendship"] = fr.Take(30).ToDictionary(k => k.Key, k => k.Value);
            var here = new SortedDictionary<string, object>();
            foreach (var n in loc.characters.OrderBy(n => Dist(n.TilePoint, p.TilePoint)).Take(15))
                here[n.Name] = new { x = n.TilePoint.X, y = n.TilePoint.Y };
            E["npcs_here"] = here;
            E["flags_new"] = Flags().Where(f => !this.StartFlags.Contains(f)).OrderBy(x => x).Take(30).ToList();
            E["mailbox"] = p.mailbox.Take(20).ToList();
            return E;
        }

        private static string SafeQuestName(StardewValley.Quests.Quest q)
        {
            try { return q.GetName(); } catch (Exception) { return q.id.Value; }
        }

        private Dictionary<string, object> ModPart()
        {
            var M = new Dictionary<string, object>
            {
                ["smapi_mods"] = new List<string>(), ["content_packs"] = new List<string>(), ["npcs"] = new Dictionary<string, object>(), ["objects"] = new Dictionary<string, object>(),
                ["items"] = new Dictionary<string, object>(), ["locations"] = new List<string>(), ["mod_data"] = new Dictionary<string, string>(),
                ["flags"] = new List<string>(), ["stats"] = new Dictionary<string, object>(), ["quests"] = new Dictionary<string, string>(),
                ["event"] = null, ["data"] = new Dictionary<string, object>()
            };
            M["smapi_mods"] = this.Helper.ModRegistry.GetAll().Where(m => !HarnessMods.Contains(m.Manifest.UniqueID))
                .Select(m => $"{m.Manifest.Name} {m.Manifest.Version} ({m.Manifest.UniqueID}{(m.IsContentPack ? ", content pack" : "")})").OrderBy(x => x).Take(20).ToList();
            // content packs SMAPI loaded, with the framework they are for (Content Patcher packs: their edits show under `data`)
            M["content_packs"] = this.Helper.ModRegistry.GetAll().Where(m => m.IsContentPack)
                .Select(m => $"{m.Manifest.Name} {m.Manifest.Version} ({m.Manifest.UniqueID}, for {m.Manifest.ContentPackFor?.UniqueID})").OrderBy(x => x).Take(20).ToList();
            try { M["data"] = this.DataDiff(); } catch (Exception e) { M["data"] = new Dictionary<string, object> { ["error"] = Cut(e.Message, 200) }; }
            if (!Context.IsWorldReady || Game1.player == null || Game1.currentLocation == null) return M;
            var p = Game1.player; var here = Game1.currentLocation;
            // NPCs that are not vanilla villagers / monsters (pets, horses, children are named by the player: left out)
            var npcs = new List<(int d, string k, object v)>();
            Utility.ForEachLocation(l =>
            {
                foreach (var n in l.characters)
                {
                    if (n is Pet || n is Horse || n is Child || n is Junimo) continue;
                    bool van = n is Monster ? this.V.Monsters.Contains(n.Name) : this.V.Npcs.Contains(n.Name);
                    if (van || this.V.Empty) continue;
                    int d = l == here ? Dist(n.TilePoint, p.TilePoint) : 100000;
                    npcs.Add((d, n.Name, new { location = l.NameOrUniqueName, x = n.TilePoint.X, y = n.TilePoint.Y, display = Cut(n.displayName, 60), kind = n.GetType().Name }));
                }
                return true;
            });
            M["npcs"] = npcs.OrderBy(t => t.d).Take(20).GroupBy(t => t.k).ToDictionary(g => g.Key, g => g.First().v);
            // objects / furniture in this location: not vanilla, or carrying mod data
            var objs = new List<(int d, string k, object v)>();
            foreach (var kv in here.objects.Pairs)
            {
                var o = kv.Value; bool van = this.V.Items.Contains(o.QualifiedItemId);
                if (van && o.modData.Length == 0) continue;
                if (!van && this.V.Empty) continue;
                objs.Add((Dist(new Point((int)kv.Key.X, (int)kv.Key.Y), p.TilePoint), $"{Cut(o.DisplayName, 50)}@{kv.Key.X},{kv.Key.Y}", new { id = o.QualifiedItemId, mod_data = MD(o.modData) }));
            }
            foreach (var f in here.furniture)
            {
                bool van = this.V.Items.Contains(f.QualifiedItemId);
                if ((van && f.modData.Length == 0) || (!van && this.V.Empty)) continue;
                objs.Add((Dist(new Point((int)f.TileLocation.X, (int)f.TileLocation.Y), p.TilePoint), $"{Cut(f.DisplayName, 50)}@{f.TileLocation.X},{f.TileLocation.Y}", new { id = f.QualifiedItemId, mod_data = MD(f.modData) }));
            }
            foreach (var kv in here.terrainFeatures.Pairs)
                if (kv.Value.modData.Length > 0)
                    objs.Add((Dist(new Point((int)kv.Key.X, (int)kv.Key.Y), p.TilePoint), $"{kv.Value.GetType().Name}@{kv.Key.X},{kv.Key.Y}", new { mod_data = MD(kv.Value.modData) }));
            M["objects"] = objs.OrderBy(t => t.d).Take(20).GroupBy(t => t.k).ToDictionary(g => g.Key, g => g.First().v);
            var items = new Dictionary<string, object>();
            foreach (var it in p.Items)
            {
                if (it == null) continue;
                bool van = this.V.Items.Contains(it.QualifiedItemId);
                if ((van && it.modData.Length == 0) || (!van && this.V.Empty)) continue;
                items[Cut(it.DisplayName, 60)] = new { id = it.QualifiedItemId, stack = it.Stack, mod_data = MD(it.modData) };
                if (items.Count >= 20) break;
            }
            M["items"] = items;
            if (!this.V.Empty)
            {
                var locs = new List<string>();
                Utility.ForEachLocation(l => { if (!this.V.Locations.Contains(l.NameOrUniqueName) && !this.V.Locations.Contains(l.Name)) locs.Add(l.NameOrUniqueName); return true; });
                M["locations"] = locs.OrderBy(x => x).Take(20).ToList();
            }
            var md = new SortedDictionary<string, string>();
            foreach (var kv in p.modData.Pairs) md["player/" + kv.Key] = Cut(kv.Value, 200);
            foreach (var kv in here.modData.Pairs) md[$"location:{here.NameOrUniqueName}/" + kv.Key] = Cut(kv.Value, 200);
            var farm = Game1.getFarm();
            if (farm != null && farm != here) foreach (var kv in farm.modData.Pairs) md["location:Farm/" + kv.Key] = Cut(kv.Value, 200);
            if (Game1.CustomData != null) foreach (var kv in Game1.CustomData) md["save/" + kv.Key] = Cut(kv.Value, 200);
            M["mod_data"] = md.Take(30).ToDictionary(k => k.Key, k => k.Value);
            if (!this.V.Empty)
            {
                M["flags"] = Flags().Where(f => !this.IsVanillaWord(f)).OrderBy(x => x).Take(30).ToList();
                var st = new SortedDictionary<string, object>();
                foreach (var kv in p.stats.Values) if (!this.IsVanillaWord(kv.Key)) st[kv.Key] = kv.Value;
                M["stats"] = st.Take(30).ToDictionary(k => k.Key, k => k.Value);
                var qs = new Dictionary<string, string>();
                foreach (var q in p.questLog) if (!this.V.Quests.Contains(q.id.Value ?? "")) qs[q.id.Value ?? "?"] = Cut(SafeQuestName(q), 200);
                M["quests"] = qs;
                string ev = Game1.eventUp ? Game1.CurrentEvent?.id : null;
                if (ev != null && !this.V.Events.Contains(ev)) M["event"] = ev;
            }
            return M;
        }

        private static Dictionary<string, string> MD(ModDataDictionary d)
        {
            var r = new Dictionary<string, string>();
            foreach (var kv in d.Pairs) { r[kv.Key] = Cut(kv.Value, 200); if (r.Count >= 10) break; }
            return r;
        }

        private static IEnumerable<string> Flags()
        {
            var p = Game1.player; if (p == null) yield break;
            foreach (var f in p.mailReceived) yield return "mail:" + f;
            foreach (var f in p.eventsSeen) yield return "event:" + f;
            foreach (var f in p.activeDialogueEvents.Keys) yield return "dialogue_event:" + f;
            foreach (var f in p.triggerActionsRun) yield return "trigger:" + f;
            foreach (var f in Game1.worldStateIDs) yield return "world:" + f;
            foreach (var f in p.mailForTomorrow) yield return "mail_tomorrow:" + f;
        }

        private bool IsVanillaWord(string flag)
        {
            if (this.VocabCache.TryGetValue(flag, out bool b)) return b;
            int i = flag.IndexOf(':'); string kind = i > 0 ? flag.Substring(0, i) : ""; string w = i > 0 ? flag.Substring(i + 1) : flag;
            if (this.V.StartFlags.Contains(flag) || this.V.Vocab.Contains(w)) b = true;
            else if (kind == "event") b = this.V.Events.Contains(w);
            else
            {
                var t = Regex.Split(w, "[^A-Za-z0-9_]+").Where(x => x.Length > 0).ToList();
                b = t.Count > 1 && t.All(x => this.V.Vocab.Contains(x));
            }
            return this.VocabCache[flag] = b;
        }

        private List<string> ScreenText()
        {
            var T = new List<string>(); var seen = new HashSet<string>();
            void add(string kind, string s)
            {
                if (string.IsNullOrWhiteSpace(s)) return;
                s = Cut(s.Replace("\n", " ").Replace("^", " ").Trim(), 200);
                if (seen.Add(s)) T.Add(kind + ": " + s);
            }
            try
            {
                if (Game1.hudMessages != null) foreach (var h in Game1.hudMessages) add("hud", h.message);
                if (Game1.activeClickableMenu is DialogueBox db)
                {
                    string who = db.characterDialogue?.speaker?.displayName;
                    add("dialog", (who != null ? who + ": " : "") + db.getCurrentString());
                    if (db.isQuestion && db.responses != null) foreach (var r in db.responses) add("choice", r.responseText);
                }
                if (Game1.activeClickableMenu is LetterViewerMenu lv && lv.mailMessage != null && lv.mailMessage.Count > 0)
                    add("letter", lv.mailMessage[Math.Clamp(lv.page, 0, lv.mailMessage.Count - 1)]);
                if (Game1.activeClickableMenu is ShopMenu sm) add("shop", sm.ShopId);
                if (Game1.chatBox?.messages != null)
                    foreach (var m in Game1.chatBox.messages.Skip(Math.Max(0, Game1.chatBox.messages.Count - 8)))
                        add("chat", string.Concat(m.message.Select(x => x.message ?? "")));
            }
            catch (Exception) { }
            int n = 0;
            foreach (var s in LastFrame)
            {
                if (n >= 30) break;
                string c = Cut(s, 200);
                if (seen.Contains(c) || T.Any(t => t.EndsWith(c))) continue;
                if (!c.Any(char.IsLetter)) continue;
                seen.Add(c); T.Add("drawn: " + c); n++;
            }
            return T;
        }

        // ------------------------------------------------------------------ data assets vs vanilla (keys added / entries changed)
        private static string Norm(string assetName)
        {
            string s = assetName.Replace('\\', '/');
            if (s.StartsWith("Data/", StringComparison.OrdinalIgnoreCase)) s = s.Substring(5);
            return s.Replace("/", "").Replace("_", "").ToLowerInvariant();
        }

        private static IEnumerable<MethodInfo> DataMethods() =>
            typeof(DataLoader).GetMethods(BindingFlags.Public | BindingFlags.Static)
                .Where(m => m.GetParameters().Length == 1 && m.GetParameters()[0].ParameterType == typeof(LocalizedContentManager));

        private static string AssetLabel(MethodInfo m) => "Data/" + m.Name.Replace("_", "/");

        private static Dictionary<string, string> HashOf(object o)
        {
            var r = new Dictionary<string, string>();
            if (o is IDictionary d)
                foreach (DictionaryEntry e in d) r[e.Key.ToString()] = Fnv(Ser(e.Value));
            else if (o is IList l)
            {
                int i = 0;
                foreach (var x in l)
                {
                    string id = null;
                    try { id = x?.GetType().GetProperty("Id")?.GetValue(x)?.ToString() ?? x?.GetType().GetField("Id")?.GetValue(x)?.ToString(); } catch (Exception) { }
                    r[id ?? ("#" + i)] = Fnv(Ser(x)); i++;
                }
            }
            else if (o != null) r["*"] = Fnv(Ser(o));
            return r;
        }

        private static string Ser(object o)
        {
            try { return JsonConvert.SerializeObject(o, JsonSet); } catch (Exception) { return o?.ToString() ?? ""; }
        }

        private static string Fnv(string s)
        {
            ulong h = 14695981039346656037UL;
            foreach (char c in s) { h ^= c; h *= 1099511628211UL; }
            return h.ToString("x16");
        }

        private static Dictionary<string, string> LoadDict(string asset)
        {
            try { return HashOf(Game1.content.Load<Dictionary<string, string>>(asset)); } catch (Exception) { return null; }
        }

        private void RefreshData()
        {
            foreach (var m in DataMethods())
            {
                string lab = AssetLabel(m);
                if (!this.AllDirty && this.DataNow.ContainsKey(lab) && !this.Dirty.Contains(Norm(lab))) continue;
                try { this.DataNow[lab] = HashOf(m.Invoke(null, new object[] { Game1.content })); } catch (Exception) { }
            }
            // events of the current location, the dialogue of every villager in Data/Characters (vanilla and added ones: a
            // dialogue edit, e.g. by a Content Patcher pack, shows wherever the farmer is) and of the NPCs here
            var extra = new List<string>();
            if (Game1.currentLocation != null) extra.Add("Data/Events/" + Game1.currentLocation.Name);
            try { foreach (var n in DataLoader.Characters(Game1.content).Keys.Take(200)) extra.Add("Characters/Dialogue/" + n); } catch (Exception) { }
            if (Game1.currentLocation != null)
                foreach (var n in Game1.currentLocation.characters.Take(12)) if (this.V.Npcs.Contains(n.Name)) extra.Add("Characters/Dialogue/" + n.Name);
            extra = extra.Distinct().ToList();
            foreach (var a in extra)
            {
                if (!this.AllDirty && this.DataNow.ContainsKey(a) && !this.Dirty.Contains(Norm(a))) continue;
                var h = LoadDict(a); if (h != null) this.DataNow[a] = h;
            }
            this.AllDirty = false; this.Dirty.Clear();
        }

        private Dictionary<string, object> DataDiff()
        {
            var R = new Dictionary<string, object>();
            if (this.V.Empty || Game1.content == null) return R;
            bool need = this.AllDirty || this.DataNow.Count == 0;
            if (!need)
            {
                foreach (var k in this.DataNow.Keys) if (this.Dirty.Contains(Norm(k))) { need = true; break; }
                if (Game1.currentLocation != null && !this.DataNow.ContainsKey("Data/Events/" + Game1.currentLocation.Name)) need = true;
            }
            if (need) this.RefreshData();
            foreach (var kv in this.DataNow)
            {
                if (!this.V.Data.TryGetValue(kv.Key, out var van))
                {
                    if (kv.Key.StartsWith("Data/Events/") || kv.Key.StartsWith("Characters/Dialogue/")) van = new Dictionary<string, string>();
                    else continue;
                }
                var added = kv.Value.Keys.Where(k => !van.ContainsKey(k)).OrderBy(x => x).ToList();
                var changed = kv.Value.Where(e => van.TryGetValue(e.Key, out var h) && h != e.Value).Select(e => e.Key).OrderBy(x => x).ToList();
                var removed = van.Keys.Where(k => !kv.Value.ContainsKey(k)).OrderBy(x => x).ToList();
                if (added.Count + changed.Count + removed.Count == 0) continue;
                var d = new Dictionary<string, object>();
                if (added.Count > 0) d["added"] = added.Take(10).Select(x => Cut(x, 80)).ToList();
                if (changed.Count > 0) d["changed"] = changed.Take(10).Select(x => Cut(x, 80)).ToList();
                if (removed.Count > 0) d["removed"] = removed.Take(10).Select(x => Cut(x, 80)).ToList();
                if (added.Count > 10 || changed.Count > 10 || removed.Count > 10) d["counts"] = new { added = added.Count, changed = changed.Count, removed = removed.Count };
                R[kv.Key] = d;
                if (R.Count >= 25) break;
            }
            return R;
        }

        // ------------------------------------------------------------------ vanilla baseline (run once on the vanilla base save)
        private object DumpVanilla(string path)
        {
            if (!Context.IsWorldReady) return new { ok = false, error = "load the vanilla save first" };
            var sw = Stopwatch.StartNew();
            var items = new HashSet<string>();
            foreach (var t in ItemRegistry.ItemTypes) foreach (var id in t.GetAllIds()) items.Add(t.Identifier + id);
            var chars = DataLoader.Characters(Game1.content);
            var npcs = new HashSet<string>(chars.Keys);
            foreach (var n in Utility.getAllCharacters()) npcs.Add(n.Name);
            var monsters = new HashSet<string>(DataLoader.Monsters(Game1.content).Keys);
            var locs = new HashSet<string>(DataLoader.Locations(Game1.content).Keys);
            Utility.ForEachLocation(l =>
            {
                locs.Add(l.NameOrUniqueName); locs.Add(l.Name);
                foreach (var o in l.objects.Values) items.Add(o.QualifiedItemId);          // e.g. the farmhouse gift box (a chest, "(O)-1")
                foreach (var f in l.furniture) items.Add(f.QualifiedItemId);
                return true;
            }, true, true);
            var data = new Dictionary<string, Dictionary<string, string>>();
            var corpus = new StringBuilder();
            foreach (var m in DataMethods())
            {
                try
                {
                    object o = m.Invoke(null, new object[] { Game1.content });
                    data[AssetLabel(m)] = HashOf(o);
                    corpus.Append(Ser(o)).Append('\n');
                }
                catch (Exception) { }
            }
            var events = new HashSet<string>();
            foreach (var l in locs.ToList())
            {
                try
                {
                    var ev = Game1.content.Load<Dictionary<string, string>>("Data/Events/" + l);
                    data["Data/Events/" + l] = HashOf(ev);
                    foreach (var kv in ev) { events.Add(kv.Key.Split('/')[0]); corpus.Append(kv.Key).Append('\n').Append(kv.Value).Append('\n'); }
                }
                catch (Exception) { }
            }
            foreach (var n in chars.Keys)
            {
                var h = LoadDict("Characters/Dialogue/" + n); if (h != null) data["Characters/Dialogue/" + n] = h;
            }
            var quests = new HashSet<string>(DataLoader.Quests(Game1.content).Keys);
            foreach (var k in DataLoader.SpecialOrders(Game1.content).Keys) quests.Add(k);
            // words: data text + string literals of the game's own code (flags the game sets in C#)
            var vocab = new HashSet<string>();
            foreach (Match mt in Regex.Matches(corpus.ToString(), @"[A-Za-z0-9_]+")) vocab.Add(mt.Value);
            try
            {
                using var fs = File.OpenRead(Path.Combine(Constants.GamePath, "Stardew Valley.dll"));
                using var pe = new PEReader(fs);
                var md = pe.GetMetadataReader();
                var h = MetadataTokens.UserStringHandle(1);
                int guard = 0;
                while (!h.IsNil && guard++ < 400000)
                {
                    string s = md.GetUserString(h);
                    foreach (Match mt in Regex.Matches(s, @"[A-Za-z0-9_]+")) vocab.Add(mt.Value);
                    h = md.GetNextHandle(h);
                }
            }
            catch (Exception e) { this.Monitor.Log("dll strings: " + e.Message, LogLevel.Warn); }
            var dump = new JObject
            {
                ["game"] = Game1.version, ["items"] = new JArray(items.OrderBy(x => x)), ["npcs"] = new JArray(npcs.OrderBy(x => x)),
                ["monsters"] = new JArray(monsters.OrderBy(x => x)), ["locations"] = new JArray(locs.OrderBy(x => x)),
                ["events"] = new JArray(events.OrderBy(x => x)), ["quests"] = new JArray(quests.OrderBy(x => x)),
                ["start_flags"] = new JArray(Flags().OrderBy(x => x)),
                ["vocab"] = new JArray(vocab.OrderBy(x => x)), ["data"] = JObject.FromObject(data)
            };
            File.WriteAllText(path + ".tmp", dump.ToString(Formatting.None));
            File.Move(path + ".tmp", path, true);
            return new { ok = true, path, items = items.Count, npcs = npcs.Count, locations = locs.Count, assets = data.Count, vocab = vocab.Count, seconds = sw.ElapsedMilliseconds / 1000.0 };
        }

        // ------------------------------------------------------------------ probe setup helpers
        private object Place(string id, int x, int y)
        {
            var loc = Game1.currentLocation; var v = new Vector2(x, y);
            var item = ItemRegistry.Create(id) as SObject;
            if (item == null) return new { ok = false, error = "not an object: " + id };
            item.TileLocation = v;
            loc.objects.Remove(v); loc.terrainFeatures.Remove(v); loc.objects.Add(v, item);
            return new { ok = true, placed = item.QualifiedItemId, name = item.Name };
        }

        private object Clear(int x1, int y1, int x2, int y2)
        {
            var loc = Game1.currentLocation; int n = 0;
            var area = new Rectangle(Math.Min(x1, x2), Math.Min(y1, y2), Math.Abs(x2 - x1) + 1, Math.Abs(y2 - y1) + 1);
            for (int x = area.Left; x < area.Right; x++)
                for (int y = area.Top; y < area.Bottom; y++)
                {
                    var v = new Vector2(x, y);
                    if (loc.objects.Remove(v)) n++;
                    if (loc.terrainFeatures.Remove(v)) n++;
                }
            n += loc.resourceClumps.RemoveWhere(c => c.getBoundingBox().Intersects(new Rectangle(area.X * 64, area.Y * 64, area.Width * 64, area.Height * 64)));
            n += loc.largeTerrainFeatures.RemoveWhere(c => c.getBoundingBox().Intersects(new Rectangle(area.X * 64, area.Y * 64, area.Width * 64, area.Height * 64)));
            return new { ok = true, removed = n };
        }

        private object TileInfo(int x, int y)
        {
            var loc = Game1.currentLocation; var v = new Vector2(x, y);
            loc.objects.TryGetValue(v, out SObject o);
            loc.terrainFeatures.TryGetValue(v, out TerrainFeature tf);
            return new
            {
                location = loc.Name, x, y, @object = o?.Name, object_id = o?.QualifiedItemId, terrain = tf?.GetType().Name,
                hoe_dirt = tf is HoeDirt, watered = tf is HoeDirt hd && hd.state.Value == 1, passable = loc.isTilePassable(v), action = loc.doesTileHaveProperty(x, y, "Action", "Buildings")
            };
        }

        private object NpcTo(string name, int x, int y, int facing)
        {
            var npc = Game1.getCharacterFromName(name);
            if (npc == null) return new { ok = false, error = "no villager " + name };
            Game1.warpCharacter(npc, Game1.currentLocation, new Vector2(x, y));
            npc.Halt(); npc.controller = null; npc.temporaryController = null; npc.followSchedule = false;
            npc.faceDirection(facing);
            return new { ok = true, location = npc.currentLocation?.Name, x = npc.TilePoint.X, y = npc.TilePoint.Y };
        }

        private object MenuInfo()
        {
            var m = Game1.activeClickableMenu;
            if (m == null) return new { menu = (string)null, dialogue_up = Game1.dialogueUp, event_up = Game1.eventUp };
            object detail = null;
            if (m is DialogueBox db)
            {
                detail = new
                {
                    text = db.getCurrentString(), speaker = db.characterDialogue?.speaker?.Name, question = db.isQuestion,
                    responses = (db.responseCC ?? new List<ClickableComponent>()).Select((c, i) => new
                    {
                        key = i < db.responses.Length ? db.responses[i].responseKey : null,
                        text = i < db.responses.Length ? db.responses[i].responseText : null,
                        x = c.bounds.X, y = c.bounds.Y, w = c.bounds.Width, h = c.bounds.Height
                    }).ToList()
                };
            }
            else if (m is ShopMenu sm)
            {
                detail = new
                {
                    shop = sm.ShopId, first = sm.currentItemIndex,
                    items = sm.forSale.Select((s, i) => new { i, name = s.DisplayName, id = (s as Item)?.QualifiedItemId, price = sm.itemPriceAndStock.TryGetValue(s, out var st) ? st.Price : -1 }).Take(60).ToList(),
                    buttons = sm.forSaleButtons.Select(c => new { x = c.bounds.X, y = c.bounds.Y, w = c.bounds.Width, h = c.bounds.Height }).ToList()
                };
            }
            return new { menu = m.GetType().Name, x = m.xPositionOnScreen, y = m.yPositionOnScreen, w = m.width, h = m.height, detail, ui_scale = Game1.options.uiScale, zoom = Game1.options.zoomLevel };
        }

        private object SkipEvent()
        {
            bool had = false;
            if (Game1.eventUp && Game1.CurrentEvent != null) { try { Game1.CurrentEvent.skipEvent(); had = true; } catch (Exception) { } }
            if (Game1.activeClickableMenu is DialogueBox) { Game1.exitActiveMenu(); Game1.dialogueUp = false; had = true; }
            return new { ok = true, skipped = had };
        }

        private static int Dist(Point a, Point b) => Math.Abs(a.X - b.X) + Math.Abs(a.Y - b.Y);

        internal static string Cut(string s, int n) => s == null ? null : (s.Length <= n ? s : s.Substring(0, n - 3) + "...");
    }

    internal class Vanilla
    {
        public bool Empty = true;
        public HashSet<string> Items = new HashSet<string>(), Npcs = new HashSet<string>(), Monsters = new HashSet<string>(),
            Locations = new HashSet<string>(), Events = new HashSet<string>(), Quests = new HashSet<string>(), Vocab = new HashSet<string>(),
            StartFlags = new HashSet<string>();
        public Dictionary<string, Dictionary<string, string>> Data = new Dictionary<string, Dictionary<string, string>>();

        public static Vanilla Load(string path)
        {
            var j = JObject.Parse(File.ReadAllText(path));
            HashSet<string> S(string k) => new HashSet<string>((j[k] as JArray ?? new JArray()).Select(x => (string)x));
            return new Vanilla
            {
                Empty = false, Items = S("items"), Npcs = S("npcs"), Monsters = S("monsters"), Locations = S("locations"), Events = S("events"),
                Quests = S("quests"), Vocab = S("vocab"), StartFlags = S("start_flags"),
                Data = (j["data"] as JObject)?.ToObject<Dictionary<string, Dictionary<string, string>>>() ?? new Dictionary<string, Dictionary<string, string>>()
            };
        }
    }
}
