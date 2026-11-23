// GameAnything-Bench regression probe bridge for Terraria (tModLoader 2026.08, Terraria 1.4.4.9).
// Inactive unless $GAPROBE is set (comma list of probe groups); results go to $GAPROBE_OUT as `GAPROBE {json}` lines.
// Server run (headless, -autocreate with a fixed seed): content, worldgen, newplayer (creates the probe player "GAProbe").
// Client run (-skipselect GAProbe:Probe): perf, inventory, move, accessory, dig, combat, time, npcs, craft, enemies, boss, save.
// Client run 2 (saveload): reads back what the save step wrote. The bridge drives the player through the game's own
// controls (ModPlayer.SetControls + the cursor position), uses the real game routines (item use, NPC.SpawnNPC, recipes ...),
// and reseeds Main.rand before each probe.
using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Microsoft.Xna.Framework;
using Terraria;
using Terraria.DataStructures;
using Terraria.GameContent;
using Terraria.ID;
using Terraria.IO;
using Terraria.ModLoader;
using Terraria.Utilities;

namespace GAProbe
{
    public static class P
    {
        public static readonly string Groups = Environment.GetEnvironmentVariable("GAPROBE") ?? "";
        public static readonly string Out = Environment.GetEnvironmentVariable("GAPROBE_OUT") ?? "";
        public static bool Active => Groups.Length > 0;
        public static bool Has(string g) => ("," + Groups + ",").Contains("," + g + ",");
        public static void Emit(string probe, Dictionary<string, object> d)
        {
            d["probe"] = probe;
            string line = "GAPROBE " + JsonSerializer.Serialize(d);
            try { File.AppendAllText(Out, line + "\n"); } catch { }
            ModContent.GetInstance<GAProbe>()?.Logger.Info(line.Length > 300 ? line.Substring(0, 300) + "..." : line);
        }
        public static string H(string s) { using var m = MD5.Create(); return Convert.ToHexString(m.ComputeHash(Encoding.UTF8.GetBytes(s))).Substring(0, 12); }
        public static double R(double v, int n = 3) => Math.Round(v, n);
        public static void Seed(int s) { Main.rand = new UnifiedRandom(s); }
        // controls (applied in GAPlayer.SetControls) and the cursor (world coordinates, applied before the players update)
        public static bool Drive, Right, Left, Jump, Use, Hook, Down;
        public static Vector2? Cursor;
        public static bool God, NoSpawn;
        public static List<Dictionary<string, object>> Hits = new(), Hurts = new();
        public static Dictionary<string, int> Spawned = new();
        public static bool CountSpawns;
        public static List<int> NewItems = new();
        public static bool WatchItems;
        public static void SetActive()
        {
            try { typeof(Microsoft.Xna.Framework.Game).GetProperty("IsActive")?.GetSetMethod(true)?.Invoke(Main.instance, new object[] { true }); } catch { }
            Main.hasFocus = true;
        }
    }

    public class GAProbe : Mod { }

    public class GAPlayer : ModPlayer
    {
        public override void SetControls()
        {
            if (!P.Drive || Player.whoAmI != Main.myPlayer) return;
            Player.controlRight = P.Right; Player.controlLeft = P.Left; Player.controlJump = P.Jump; Player.controlUseItem = P.Use;
            Player.controlHook = P.Hook; Player.controlDown = P.Down; Player.controlUp = false; Player.controlInv = false;
            Player.controlThrow = false; Player.controlMount = false; Player.controlSmart = false;
        }
        public override void PreUpdate()
        {
            if (P.God && Player.whoAmI == Main.myPlayer) { Player.statLife = Player.statLifeMax2; Player.immune = true; Player.immuneTime = 2; }
        }
        public override void OnHurt(Player.HurtInfo info)
        {
            if (Player.whoAmI == Main.myPlayer)
                P.Hurts.Add(new() { ["damage"] = info.Damage, ["knockback"] = P.R(info.Knockback), ["dir"] = info.HitDirection });
        }
    }

    public class GANPC : GlobalNPC
    {
        public override void OnHitByItem(NPC npc, Player player, Item item, NPC.HitInfo hit, int damageDone)
        {
            P.Hits.Add(new() { ["npc"] = npc.type, ["item"] = item.type, ["damage"] = hit.Damage, ["source"] = hit.SourceDamage, ["crit"] = hit.Crit, ["kb"] = P.R(hit.Knockback), ["dir"] = hit.HitDirection });
        }
        public override void EditSpawnRate(Player player, ref int spawnRate, ref int maxSpawns) { if (P.NoSpawn) { maxSpawns = 0; spawnRate = int.MaxValue; } }
        public override void OnSpawn(NPC npc, IEntitySource source)
        {
            if (!P.CountSpawns) return;
            string k = npc.type < NPCID.Count ? NPCID.Search.GetName(npc.type) : (npc.ModNPC?.FullName ?? npc.type.ToString());
            P.Spawned[k] = P.Spawned.TryGetValue(k, out var c) ? c + 1 : 1;
        }
    }

    public class GAItem : GlobalItem
    {
        public override void OnSpawn(Item item, IEntitySource source) { if (P.WatchItems) P.NewItems.Add(item.type); }
    }

    public class GASystem : ModSystem
    {
        IEnumerator run; int wait; int ticks; bool started;

        // ------------------------------------------------------------ content (any side, after all mods loaded)
        // content is dumped on the server's world load: after every mod's SetupContent, AddRecipes and PostAddRecipes

        static string Dt(Item i) => i.DamageType?.FullName ?? "";
        void DumpContent()
        {
            var items = new Dictionary<string, string>(); var names = new Dictionary<string, string>();
            for (int t = 1; t < ItemID.Count; t++)
            {
                var i = ContentSamples.ItemsByType[t];
                string s = string.Join("|", i.type, i.damage, i.useTime, i.useAnimation, P.R(i.knockBack), i.crit, i.defense, i.value, i.rare, i.maxStack,
                    i.width, i.height, i.shoot, P.R(i.shootSpeed), i.useAmmo, i.ammo, i.pick, i.axe, i.hammer, i.createTile, i.createWall, i.placeStyle,
                    i.healLife, i.healMana, i.mana, i.accessory, i.autoReuse, Dt(i), P.R(i.scale), i.useStyle, i.consumable, i.buffType, i.buffTime,
                    i.headSlot, i.bodySlot, i.legSlot, i.vanity, i.noMelee, i.channel, i.tileBoost, i.fishingPole, i.bait);
                items[t.ToString()] = P.H(s);
                names[t.ToString()] = Lang.GetItemNameValue(t);
            }
            var npcs = new Dictionary<string, string>();
            for (int t = -65; t < NPCID.Count; t++)
            {
                if (t == 0) continue;
                if (!ContentSamples.NpcsByNetId.TryGetValue(t, out var n)) continue;
                { var n2 = new NPC(); n2.SetDefaults(t); var n3 = new NPC(); n3.SetDefaults(t);
                  if (n2.lifeMax != n3.lifeMax || n2.damage != n3.damage || n2.defense != n3.defense || n2.scale != n3.scale || n2.value != n3.value) { npcs[t.ToString()] = "random"; continue; }
                  n = n2; }
                string s = string.Join("|", n.netID, n.lifeMax, n.damage, n.defense, P.R(n.knockBackResist), n.aiStyle, n.width, n.height, P.R(n.value),
                    n.noGravity, n.noTileCollide, n.friendly, n.townNPC, n.boss, P.R(n.scale), n.lavaImmune, string.Join(",", n.buffImmune.Select((b, k) => b ? k : -1).Where(k => k >= 0).Take(400)));
                npcs[t.ToString()] = P.H(s);
            }
            var projs = new Dictionary<string, string>();
            Func<int, string> psig = t => { var p = new Projectile(); p.SetDefaults(t);
                return string.Join("|", p.type, p.aiStyle, p.width, p.height, p.friendly, p.hostile, p.penetrate, p.timeLeft, p.tileCollide, p.ignoreWater, p.extraUpdates, p.DamageType?.FullName, P.R(p.scale), p.light); };
            for (int t = 1; t < ProjectileID.Count; t++)
            {
                string a = psig(t), b = psig(t);
                projs[t.ToString()] = a == b ? P.H(a) : "random";   // SetDefaults rolls random values for a few projectiles
            }
            var tiles = new Dictionary<string, string>();
            for (int t = 0; t < TileID.Count; t++)
                tiles[t.ToString()] = P.H(string.Join("|", Main.tileSolid[t], Main.tileSolidTop[t], Main.tileFrameImportant[t], Main.tileNoAttach[t], Main.tileLavaDeath[t],
                    Main.tileBlockLight[t], Main.tileLighted[t], Main.tileCut[t], Main.tileTable[t], Main.tileContainer[t], Main.tileMergeDirt[t], Main.tileStone[t], Main.tileAxe[t], Main.tileHammer[t], Main.tileDungeon[t]));
            var recipes = new List<string>(); int modRecipes = 0;
            for (int r = 0; r < Recipe.numRecipes; r++)
            {
                var rc = Main.recipe[r];
                if (rc.Disabled) continue;
                if (rc.Mod != null) { modRecipes++; continue; }
                string s = rc.createItem.type + "x" + rc.createItem.stack + " <- " + string.Join(",", rc.requiredItem.Select(i => i.type + "x" + i.stack).OrderBy(x => x))
                    + " @ " + string.Join(",", rc.requiredTile.OrderBy(x => x)) + " ? " + string.Join(",", rc.Conditions.Select(c => c.Description.Key).OrderBy(x => x))
                    + " g " + string.Join(",", rc.acceptedGroups.OrderBy(x => x));
                recipes.Add(s);
            }
            P.Emit("content", new()
            {
                ["n_items"] = ItemLoader.ItemCount, ["n_npcs"] = NPCLoader.NPCCount, ["n_projectiles"] = ProjectileLoader.ProjectileCount, ["n_tiles"] = TileLoader.TileCount,
                ["n_walls"] = WallLoader.WallCount, ["n_buffs"] = BuffLoader.BuffCount, ["n_recipes"] = Recipe.numRecipes, ["n_mod_recipes"] = modRecipes,
                ["mods"] = ModLoader.Mods.Select(m => m.Name + " " + m.Version).ToList(),
                ["items"] = items, ["item_names"] = names, ["npcs"] = npcs, ["projectiles"] = projs, ["tiles"] = tiles, ["recipes"] = recipes,
            });
        }

        // ------------------------------------------------------------ server: worldgen + new player
        public override void OnWorldLoad()
        {
            if (!P.Active) return;
            if (Main.dedServ)
            {
                try { if (P.Has("content")) DumpContent(); } catch (Exception e) { P.Emit("content", new() { ["error"] = e.ToString() }); }
                try { if (P.Has("worldgen")) DumpWorld(); } catch (Exception e) { P.Emit("worldgen", new() { ["error"] = e.ToString() }); }
                try { if (P.Has("newplayer")) NewPlayer(); } catch (Exception e) { P.Emit("newplayer", new() { ["error"] = e.ToString() }); }
                P.Emit("_done", new());
            }
            else { run = null; started = false; ticks = 0; }
        }

        void DumpWorld()
        {
            int sx = Main.spawnTileX, sy = Main.spawnTileY;
            var counts = new Dictionary<string, long>(); var sb = new StringBuilder(); var sbw = new StringBuilder();
            long solid = 0, liquid = 0;
            for (int x = 0; x < Main.maxTilesX; x++)
                for (int y = 0; y < Main.maxTilesY; y++)
                {
                    var t = Main.tile[x, y];
                    if (t.HasTile) { string k = t.TileType.ToString(); counts[k] = counts.TryGetValue(k, out var c) ? c + 1 : 1; solid++; }
                    if (t.LiquidAmount > 0) liquid++;
                }
            // the area around spawn: 200 x (surface region) tiles, type + wall + liquid
            for (int x = Math.Max(0, sx - 100); x < Math.Min(Main.maxTilesX, sx + 100); x++)
                for (int y = Math.Max(0, sy - 60); y < Math.Min(Main.maxTilesY, sy + 60); y++)
                {
                    var t = Main.tile[x, y];
                    sb.Append(t.HasTile ? t.TileType : -1).Append(',').Append(t.WallType).Append(',').Append(t.LiquidAmount).Append(';');
                }
            var npcs = new List<string>();
            for (int i = 0; i < Main.maxNPCs; i++) if (Main.npc[i].active) npcs.Add(NPCID.Search.GetName(Main.npc[i].type));
            P.Emit("worldgen", new()
            {
                ["size"] = Main.maxTilesX + "x" + Main.maxTilesY, ["seed"] = Main.ActiveWorldFileData?.SeedText, ["spawn"] = sx + "," + sy,
                ["world_surface"] = (int)Main.worldSurface, ["rock_layer"] = (int)Main.rockLayer, ["dungeon"] = Main.dungeonX + "," + Main.dungeonY,
                ["crimson"] = WorldGen.crimson, ["spawn_area_hash"] = P.H(sb.ToString()), ["tiles_total"] = solid, ["liquid_tiles"] = liquid,
                ["tile_counts"] = counts, ["chests"] = Main.chest.Count(c => c != null), ["npcs"] = npcs, ["tile_hash_all"] = P.H(string.Join(";", counts.OrderBy(k => k.Key).Select(k => k.Key + ":" + k.Value))),
            });
        }

        void NewPlayer()
        {
            // what the character creation screen does for a Classic (difficulty 0) character (UICharacterCreation.SetupPlayerStatsAndInventoryBasedOnDifficulty)
            P.Seed(7);
            var p = new Player(); p.name = "GAProbe"; p.difficulty = 0;
            int n = 0;
            p.inventory[n].SetDefaults(ItemID.CopperShortsword); p.inventory[n++].Prefix(-1);
            p.inventory[n].SetDefaults(ItemID.CopperPickaxe); p.inventory[n++].Prefix(-1);
            p.inventory[n].SetDefaults(ItemID.CopperAxe); p.inventory[n++].Prefix(-1);
            p.savedPerPlayerFieldsThatArentInThePlayerClass = new Player.SavedPlayerDataWithAnnoyingRules();
            var vanilla = p.inventory.Where(i => !i.IsAir).Select(i => i.Clone());
            var start = PlayerLoader.GetStartingItems(p, vanilla);
            PlayerLoader.SetStartInventory(p, start);
            // PlayerFileData.CreateAndSave, minus Main.Achievements.Save() (null on a dedicated server); always a local (non-cloud) file
            Directory.CreateDirectory(Main.PlayerPath);
            var fd = new PlayerFileData(Main.GetPlayerPathFromName(p.name, false), false) { Metadata = FileMetadata.FromCurrentSettings(FileType.Player), Player = p };
            typeof(Player).GetMethod("InternalSavePlayerFile", BindingFlags.NonPublic | BindingFlags.Static).Invoke(null, new object[] { fd });
            P.Emit("newplayer", new() { ["path"] = fd.Path, ["cloud"] = fd.IsCloudSave, ["start_items"] = p.inventory.Where(i => !i.IsAir).Select(i => i.type + "x" + i.stack).ToList() });
        }

        // ------------------------------------------------------------ client: in-world probes
        public override void PreUpdatePlayers()
        {
            if (!P.Active || Main.dedServ || Main.gameMenu) return;
            P.SetActive();
            if (P.Cursor.HasValue) { Main.mouseX = (int)(P.Cursor.Value.X - Main.screenPosition.X); Main.mouseY = (int)(P.Cursor.Value.Y - Main.screenPosition.Y); }
            if (P.Drive) { Main.playerInventory = false; Main.LocalPlayer.mouseInterface = false; }
        }

        public override void PostDrawTiles() { if (P.Active && !Main.dedServ) P.SetActive(); }

        public override void PostUpdateEverything()
        {
            if (!P.Active || Main.dedServ || Main.gameMenu) return;
            ticks++;
            if (!started) { if (ticks < 90) return; started = true; run = Main_(); }
            if (wait > 0) { wait--; return; }
            try
            {
                if (run != null && run.MoveNext()) { if (run.Current is int w) wait = Math.Max(0, w - 1); }
                else run = null;
            }
            catch (Exception e) { P.Emit("_fatal", new() { ["error"] = e.ToString() }); run = null; P.Drive = false; P.God = false; }
        }

        // ---- helpers
        static Player Me => Main.LocalPlayer;
        static int AX, AY;   // arena: floor top row AY (stone), clear air above, centred on AX
        static void Release() { P.Right = P.Left = P.Jump = P.Use = P.Hook = P.Down = false; }
        static void Stand(int tx) { Me.Teleport(new Vector2(tx * 16 + 8 - Me.width / 2f, AY * 16 - Me.height), -1); Me.velocity = Vector2.Zero; Me.fallStart = (int)(Me.position.Y / 16f); }
        static void ClearNPCs(bool keepTown = true) { for (int i = 0; i < Main.maxNPCs; i++) if (Main.npc[i].active && !(keepTown && (Main.npc[i].townNPC || Main.npc[i].type == NPCID.OldMan))) Main.npc[i].active = false; }
        static void ClearItems() { for (int i = 0; i < Main.maxItems; i++) Main.item[i].active = false; }
        static void Hold(int type, int stack = 1) { Me.inventory[9] = new Item(type, stack); Me.selectedItem = 9; }
        static void SetTile(int x, int y, int type) { var t = Main.tile[x, y]; t.ClearEverything(); WorldGen.PlaceTile(x, y, type, true, true); WorldGen.SquareTileFrame(x, y); }
        static void BuildArena()
        {
            AX = Main.spawnTileX; AY = Math.Max(80, Math.Min((int)(Main.worldSurface * 0.5), Main.spawnTileY - 60));
            for (int x = AX - 90; x <= AX + 90; x++)
                for (int y = AY - 70; y <= AY + 3; y++) Main.tile[x, y].ClearEverything();
            for (int x = AX - 90; x <= AX + 90; x++) { SetTile(x, AY, TileID.Stone); SetTile(x, AY + 1, TileID.Stone); }
            for (int x = AX + 30; x <= AX + 50; x++) SetTile(x, AY - 12, TileID.Stone);   // grapple ceiling
            for (int x = AX - 90; x <= AX + 90; x++) for (int y = AY - 72; y <= AY + 4; y++) WorldGen.SquareTileFrame(x, y);
        }
        static double Top(Player p) => p.position.Y;

        IEnumerator Main_()
        {
            P.Drive = true; Release(); P.Cursor = null;
            Main.autoPause = false; Main.playerInventory = false;
            yield return 2;
            var r = new Dictionary<string, object> { ["player"] = Me.name, ["world"] = Main.worldName, ["mods"] = ModLoader.Mods.Select(m => m.Name).ToList() };
            P.Emit("_entered", r);
            if (P.Has("saveload")) { foreach (var x in SaveLoad()) yield return x; P.Drive = false; P.Emit("_done", new()); Main.instance.Exit(); yield break; }
            if (P.Has("inventory")) Run("inventory", Inventory);   // before anything changes the player
            BuildArena(); ClearNPCs(); yield return 30;
            Main.dayTime = true; Main.time = 13500; Main.bloodMoon = false; Main.eclipse = false; Main.raining = false; Main.StopRain(); Main.invasionType = 0;
            var groups = new (string, Func<IEnumerable>)[] { ("perf", Perf), ("move", Move), ("accessory", Accessory), ("dig", Dig), ("combat", Combat), ("time", Time),
                ("npcs", Npcs), ("craft", Craft), ("enemies", Enemies), ("boss", Boss), ("save", Save) };
            foreach (var (g, f) in groups)
            {
                if (!P.Has(g)) continue;
                P.Seed(1000 + g.Length); Release(); P.Cursor = null; P.God = false; ClearItems();
                P.NoSpawn = g != "enemies"; ClearNPCs();
                Me.statLife = Me.statLifeMax2; Me.velocity = Vector2.Zero;
                var d = new Dictionary<string, object>(); Cur = d; bool fail = false;
                IEnumerator e = f().GetEnumerator();
                while (true)
                {
                    object cur;
                    try { if (!e.MoveNext()) break; cur = e.Current; }
                    catch (Exception ex) { d["error"] = ex.GetType().Name + ": " + ex.Message + " @ " + ex.StackTrace?.Split('\n').FirstOrDefault()?.Trim(); fail = true; break; }
                    yield return cur;
                }
                Release(); P.God = false; P.CountSpawns = false; P.WatchItems = false;
                if (g == "save") yield break;   // Save() quits to the menu and exits
                P.Emit(g, d); _ = fail;
                yield return 5;
            }
            P.Drive = false; P.Emit("_done", new());
            Main.instance.Exit();
        }
        static Dictionary<string, object> Cur;
        static void Run(string name, Func<Dictionary<string, object>> f)
        {
            Dictionary<string, object> d;
            try { d = f(); } catch (Exception e) { d = new() { ["error"] = e.ToString() }; }
            P.Emit(name, d);
        }

        // ---- inventory: the new player's items and slots as loaded from the save made by the server run
        static Dictionary<string, object> Inventory()
        {
            return new()
            {
                ["items"] = Me.inventory.Select((it, k) => it.IsAir ? null : k + ":" + it.type + "x" + it.stack).Where(s => s != null).ToList(),
                ["armor"] = Me.armor.Select((it, k) => it.IsAir ? null : k + ":" + it.type).Where(s => s != null).ToList(),
                ["n_inventory"] = Me.inventory.Length, ["n_armor"] = Me.armor.Length, ["n_dye"] = Me.dye.Length, ["n_misc"] = Me.miscEquips.Length,
                ["accessory_slots"] = Me.extraAccessorySlots + 5, ["life_max"] = Me.statLifeMax, ["life_max2"] = Me.statLifeMax2, ["mana_max"] = Me.statManaMax2,
                ["defense"] = Me.statDefense.Positive,
            };
        }

        // ---- perf: updates per second and the game's own update timer, idle in the arena
        IEnumerable Perf()
        {
            Stand(AX); yield return 60;
            var sw = System.Diagnostics.Stopwatch.StartNew(); var up = new List<double>(); var fr = new List<double>();
            for (int i = 0; i < 300; i++) { up.Add(Main.upTimer); fr.Add(Main.frameRate); yield return 1; }
            double s = sw.Elapsed.TotalSeconds;
            up.Sort(); fr.Sort();
            Cur["ups"] = P.R(300 / s, 1); Cur["update_ms_median"] = P.R(up[150], 2); Cur["fps_median"] = fr[150];
        }

        // ---- move: run, stop, jump, short hop, fall speed, fall damage, grapple (flat stone floor in the sky arena)
        IEnumerable Move()
        {
            var d = Cur;
            Stand(AX - 60); yield return 20;
            double x0 = Me.position.X; var vs = new List<double>();
            P.Right = true;
            for (int i = 0; i < 120; i++) { yield return 1; vs.Add(Me.velocity.X); }
            P.Right = false;
            double top = vs.Max(); int f95 = vs.FindIndex(v => v >= 0.95 * top) + 1;
            double dist60 = 0; { double xx = x0; } // distance in the first 60 ticks
            d["run_top_speed"] = P.R(top); d["run_ticks_to_95pct"] = f95;
            int stopT = 0; double xs = Me.position.X;
            for (int i = 0; i < 120 && Math.Abs(Me.velocity.X) > 0.01; i++) { yield return 1; stopT++; }
            d["stop_ticks"] = stopT; d["stop_dist"] = P.R((Me.position.X - xs) / 16.0, 2);
            d["run_dist_2s_tiles"] = P.R((xs - x0) / 16.0, 1);
            // full jump (hold)
            Stand(AX); yield return 20;
            double y0 = Top(Me); double minY = y0; int air = 0;
            P.Jump = true;
            for (int i = 0; i < 120; i++) { yield return 1; minY = Math.Min(minY, Top(Me)); if (Me.velocity.Y != 0 || Top(Me) < y0 - 0.5) air++; else if (i > 3) break; }
            P.Jump = false;
            d["jump_height_tiles"] = P.R((y0 - minY) / 16.0, 2); d["jump_air_ticks"] = air;
            yield return 20;
            // short hop: jump held 2 ticks
            Stand(AX); yield return 20; y0 = Top(Me); minY = y0;
            P.Jump = true; yield return 2; P.Jump = false;
            for (int i = 0; i < 80; i++) { yield return 1; minY = Math.Min(minY, Top(Me)); }
            d["hop_height_tiles"] = P.R((y0 - minY) / 16.0, 2);
            // fall: from 30 tiles up, terminal speed and fall damage (no other damage source)
            Stand(AX); yield return 10;
            Me.statLife = Me.statLifeMax2; int life0 = Me.statLife; P.Hurts.Clear();
            Me.Teleport(new Vector2(Me.position.X, (AY - 30) * 16 - Me.height), -1); Me.velocity = Vector2.Zero; Me.fallStart = (int)(Me.position.Y / 16f);
            double vmax = 0; int fallT = 0;
            for (int i = 0; i < 200; i++) { yield return 1; vmax = Math.Max(vmax, Me.velocity.Y); fallT++; if (Me.velocity.Y == 0 && i > 5) break; }
            yield return 5;
            d["fall_max_speed"] = P.R(vmax); d["fall_ticks_30"] = fallT; d["fall_damage_30"] = life0 - Me.statLife;
            Me.statLife = Me.statLifeMax2;
            // grapple: Grappling Hook in the hook slot, cursor straight up at the ceiling 12 tiles above
            var keep = Me.miscEquips[4]; Me.miscEquips[4] = new Item(ItemID.GrapplingHook);
            Stand(AX + 40); yield return 15;
            y0 = Top(Me);
            P.Cursor = new Vector2(Me.Center.X, (AY - 11) * 16); yield return 2;
            P.Hook = true; yield return 1; P.Hook = false;
            int hookType = -1, latched = -1;
            for (int i = 0; i < 90; i++)
            {
                yield return 1;
                for (int k = 0; k < Main.maxProjectiles; k++) if (Main.projectile[k].active && Main.projectile[k].owner == Main.myPlayer && Main.projectile[k].aiStyle == 7) hookType = Main.projectile[k].type;
                if (latched < 0 && Me.grapCount > 0) latched = i + 1;
            }
            d["grapple_hook_proj"] = hookType; d["grapple_latched_tick"] = latched; d["grapple_rise_tiles"] = P.R((y0 - Top(Me)) / 16.0, 1);
            Me.RemoveAllGrapplingHooks(); Me.miscEquips[4] = keep; P.Cursor = null;
            yield return 30;
        }

        // ---- accessories: Hermes Boots (run speed) + Cloud in a Bottle (double jump) in the accessory slots
        IEnumerable Accessory()
        {
            var d = Cur;
            var k3 = Me.armor[3]; var k4 = Me.armor[4];
            Me.armor[3] = new Item(ItemID.HermesBoots); Me.armor[4] = new Item(ItemID.CloudinaBottle);
            Stand(AX - 86); yield return 20;
            var vs = new List<double>(); P.Right = true;
            for (int i = 0; i < 280; i++) { yield return 1; vs.Add(Me.velocity.X); }
            P.Right = false; yield return 60;
            d["boots_top_speed"] = P.R(vs.Max(), 2); d["boots_acc_run_speed"] = P.R(Me.accRunSpeed);
            d["boots_ticks_to_5"] = vs.FindIndex(v => v >= 5.0) + 1;
            Stand(AX); yield return 20;
            double y0 = Top(Me), minY = y0;
            P.Jump = true; yield return 25; P.Jump = false; yield return 3;
            P.Jump = true;
            for (int i = 0; i < 40; i++) { yield return 1; minY = Math.Min(minY, Top(Me)); }
            P.Jump = false;
            for (int i = 0; i < 80; i++) { yield return 1; minY = Math.Min(minY, Top(Me)); }
            d["double_jump_height_tiles"] = P.R((y0 - minY) / 16.0, 2);
            Me.armor[3] = k3; Me.armor[4] = k4;
            yield return 60;
        }

        // ---- dig / place: the copper pickaxe mines dirt and stone (ticks + dropped item), a dirt block and a work bench are placed by item use
        IEnumerable Dig()
        {
            var d = Cur;
            int fx = AX - 20;
            foreach (var (name, tile) in new[] { ("dirt", TileID.Dirt), ("stone", TileID.Stone), ("copper", TileID.Copper), ("wood", TileID.WoodBlock) })
            {
                Stand(fx); yield return 30;
                int tx = fx + 2, ty = AY - 1;
                SetTile(tx, ty, tile); ClearItems();
                Hold(ItemID.CopperPickaxe); P.Cursor = new Vector2(tx * 16 + 8, ty * 16 + 8); yield return 3; for (int w_ = 0; w_ < 90 && (Player.tileTargetX != tx || Player.tileTargetY != ty || Me.itemAnimation > 0); w_++) yield return 1;
                P.WatchItems = true; P.NewItems.Clear(); P.Use = true; int t = 0;
                for (; t < 300 && Main.tile[tx, ty].HasTile; t++) yield return 1;
                P.Use = false; yield return 20; P.WatchItems = false;
                d[name + "_ticks"] = Main.tile[tx, ty].HasTile ? -1 : t;
                d[name + "_drops"] = P.NewItems.Distinct().OrderBy(x => x).ToList();
                yield return 20;
            }
            // pick up the last drop: walk-free (items in range are picked up)
            Stand(fx); ClearItems(); yield return 10;
            int px = fx + 2, py = AY - 1;
            Main.tile[px, py].ClearEverything(); WorldGen.SquareTileFrame(px, py);
            Hold(ItemID.DirtBlock, 10); P.Cursor = new Vector2(px * 16 + 8, py * 16 + 8); yield return 3; for (int w_ = 0; w_ < 90 && (Player.tileTargetX != px || Player.tileTargetY != py || Me.itemAnimation > 0); w_++) yield return 1;
            P.Use = true; yield return 2; P.Use = false; yield return 20;
            d["place_dirt"] = Main.tile[px, py].HasTile ? (int)Main.tile[px, py].TileType : -1; d["place_dirt_left"] = Me.inventory[9].stack;
            Main.tile[px, py].ClearEverything(); WorldGen.SquareTileFrame(px, py);
            int bx = fx + 3, by = AY - 1;
            Hold(ItemID.WorkBench, 1); P.Cursor = new Vector2(bx * 16 + 8, by * 16 + 8); yield return 3; for (int w_ = 0; w_ < 90 && (Player.tileTargetX != bx || Player.tileTargetY != by || Me.itemAnimation > 0); w_++) yield return 1;
            P.Use = true; yield return 2; P.Use = false; yield return 20;
            d["place_workbench"] = Main.tile[bx, by].HasTile ? (int)Main.tile[bx, by].TileType : (Main.tile[bx - 1, by].HasTile ? (int)Main.tile[bx - 1, by].TileType : -1);
            d["place_workbench_left"] = Me.inventory[9].IsAir ? 0 : Me.inventory[9].stack;
            // axe on a tree is not in the sky arena; hammer a wall instead: Wooden Hammer removes a placed wood wall
            Main.tile[bx, by - 3].ClearEverything(); WorldGen.PlaceWall(bx, by - 3, WallID.Wood, true); WorldGen.SquareWallFrame(bx, by - 3);
            Hold(ItemID.WoodenHammer); P.Cursor = new Vector2(bx * 16 + 8, (by - 3) * 16 + 8); yield return 3; for (int w_ = 0; w_ < 90 && (Player.tileTargetX != bx || Player.tileTargetY != by - 3 || Me.itemAnimation > 0); w_++) yield return 1;
            P.Use = true; int th = 0; for (; th < 300 && Main.tile[bx, by - 3].WallType != 0; th++) yield return 1; P.Use = false;
            d["hammer_wall_ticks"] = Main.tile[bx, by - 3].WallType != 0 ? -1 : th;
            Me.inventory[9] = new Item(); P.Cursor = null;
            for (int x = fx - 2; x <= fx + 6; x++) for (int y = AY - 5; y < AY; y++) { Main.tile[x, y].ClearEverything(); WorldGen.SquareTileFrame(x, y); }
            yield return 10;
        }

        // ---- combat: Copper Broadsword hits a Blue Slime (damage, knockback, NPC immunity), the slime's contact damage and the player's immunity frames, slime loot
        IEnumerable Combat()
        {
            var d = Cur;
            ClearNPCs(); Stand(AX); yield return 10;
            int s = NPC.NewNPC(Me.GetSource_FromThis(), (int)Me.Center.X + 40, AY * 16 - 2, NPCID.BlueSlime);
            var npc = Main.npc[s];
            d["slime_life_max"] = npc.lifeMax; d["slime_damage"] = npc.damage; d["slime_defense"] = npc.defense;
            Hold(ItemID.CopperBroadsword); d["sword_damage"] = Me.GetWeaponDamage(Me.inventory[9]); d["sword_use_time"] = Me.inventory[9].useAnimation;
            d["sword_knockback"] = P.R(Me.GetWeaponKnockback(Me.inventory[9])); d["sword_crit"] = Me.GetWeaponCrit(Me.inventory[9]);
            P.God = true; P.Hits.Clear();
            var hitsOk = new List<int>(); double kbv = 0;
            for (int sw = 0; sw < 4 && npc.active; sw++)
            {
                npc.Center = new Vector2(Me.Center.X + 30, npc.Center.Y); npc.velocity = Vector2.Zero;
                P.Cursor = new Vector2(Me.Center.X + 60, Me.Center.Y);
                P.Use = true; yield return 1; P.Use = false;
                for (int i = 0; i < 6; i++) { yield return 1; kbv = Math.Max(kbv, Math.Abs(npc.velocity.X)); }
                yield return 30;
            }
            d["hits"] = P.Hits.Count; d["hit_damage_source"] = P.Hits.Select(h => h["source"]).Distinct().ToList();
            d["hit_damage_mean"] = P.Hits.Count > 0 ? P.R(P.Hits.Average(h => (int)h["damage"] / ((bool)h["crit"] ? 2.0 : 1.0)), 2) : 0;
            d["hit_knockback"] = P.Hits.Select(h => h["kb"]).Distinct().ToList(); d["slime_kb_speed"] = P.R(kbv, 2);
            // contact damage: put a fresh slime on the player (no god mode), then the immunity time
            ClearNPCs(); P.God = false; Me.statLife = Me.statLifeMax2; Me.immune = false; Me.immuneTime = 0; P.Hurts.Clear(); yield return 2;
            Stand(AX); yield return 3;
            s = NPC.NewNPC(Me.GetSource_FromThis(), (int)Me.Center.X, AY * 16 - 2, NPCID.BlueSlime); npc = Main.npc[s];
            int life0 = Me.statLife; int immune = 0; double pkb = 0;
            for (int i = 0; i < 60 && P.Hurts.Count == 0; i++) { npc.Center = Me.Center; yield return 1; }
            if (P.Hurts.Count > 0) { immune = Me.immuneTime; pkb = Math.Abs(Me.velocity.X); }
            d["contact_hurt"] = P.Hurts.Count > 0; d["contact_damage"] = P.Hurts.Count > 0 ? P.Hurts[0]["damage"] : -1;
            d["contact_life_lost"] = life0 - Me.statLife; d["player_immune_ticks"] = immune; d["player_kb_speed"] = P.R(pkb, 2);
            // loot: kill a slime by strike, collect its drops
            ClearItems(); P.WatchItems = true; P.NewItems.Clear(); P.Seed(42);
            npc.StrikeInstantKill(); yield return 10; P.WatchItems = false;
            d["slime_loot"] = P.NewItems.Distinct().OrderBy(x => x).ToList();
            Me.statLife = Me.statLifeMax2; Me.inventory[9] = new Item(); P.Cursor = null; ClearNPCs(); ClearItems();
            yield return 30;
        }

        // ---- time: day/night advance, dusk and dawn transitions, moon phase
        IEnumerable Time()
        {
            var d = Cur;
            Main.dayTime = true; Main.time = 1000; yield return 1;
            double t0 = Main.time; yield return 300;
            d["day_rate"] = P.R(Main.dayRate); d["ticks_per_300"] = P.R(Main.time - t0, 1);
            Main.time = Main.dayLength - 10; yield return 20;
            d["dusk_is_night"] = !Main.dayTime; d["dusk_time"] = P.R(Main.time, 0); d["night_length"] = Main.nightLength;
            int mp = Main.moonPhase;
            Main.time = Main.nightLength - 10; yield return 20;
            d["dawn_is_day"] = Main.dayTime; d["moon_phase_step"] = (Main.moonPhase - mp + 8) % 8; d["day_length"] = Main.dayLength;
            Main.dayTime = true; Main.time = 13500;
        }

        // ---- town NPCs: the Guide is there and talks; shop lists of stock town NPCs; a built house passes the housing check and a Merchant moves in
        IEnumerable Npcs()
        {
            var d = Cur;
            int gi = -1; for (int i = 0; i < Main.maxNPCs; i++) if (Main.npc[i].active && Main.npc[i].type == NPCID.Guide) gi = i;
            d["guide"] = gi >= 0;
            if (gi >= 0)
            {
                Me.SetTalkNPC(gi); string chat = Main.npc[gi].GetChat(); Main.npcChatText = chat;
                d["guide_talks"] = !string.IsNullOrEmpty(chat); d["guide_name_set"] = !string.IsNullOrEmpty(Main.npc[gi].GivenName);
                yield return 10; Me.SetTalkNPC(-1); Main.npcChatText = "";
            }
            var shops = new Dictionary<string, object>();
            foreach (int t in new[] { NPCID.Merchant, NPCID.ArmsDealer, NPCID.Dryad, NPCID.Demolitionist, NPCID.GoblinTinkerer, NPCID.Painter, NPCID.DyeTrader, NPCID.Stylist, NPCID.Golfer, NPCID.BestiaryGirl, NPCID.Clothier, NPCID.Mechanic })
            {
                if (!NPCShopDatabase.TryGetNPCShop(NPCShopDatabase.GetShopName(t), out var shop)) { shops[NPCID.Search.GetName(t)] = "none"; continue; }
                var n = new NPC(); n.SetDefaults(t);
                var list = new List<Item>(); shop.FillShop(list, n);
                shops[NPCID.Search.GetName(t)] = list.Where(i => !i.IsAir).Select(i => i.type + "@" + (i.shopCustomPrice ?? i.value)).ToList();
            }
            d["shops"] = shops;
            // house: 12 x 7 outside (wood blocks), wood wall, door, torch, table, chair; then the housing check and a Merchant moves in
            int hx = AX + 60, hy = AY - 1;   // floor row = AY (stone)
            for (int x = hx; x < hx + 12; x++) for (int y = hy - 7; y <= hy; y++) Main.tile[x, y].ClearEverything();
            for (int x = hx; x < hx + 12; x++) SetTile(x, hy - 7, TileID.WoodBlock);
            for (int y = hy - 6; y <= hy; y++) { SetTile(hx, y, TileID.WoodBlock); if (y < hy - 2) SetTile(hx + 11, y, TileID.WoodBlock); }
            for (int x = hx + 1; x < hx + 11; x++) for (int y = hy - 6; y <= hy; y++) WorldGen.PlaceWall(x, y, WallID.Wood, true);
            for (int y = hy - 2; y <= hy; y++) Main.tile[hx + 11, y].ClearEverything();
            WorldGen.PlaceDoor(hx + 11, hy - 1, TileID.ClosedDoor);
            WorldGen.PlaceTile(hx + 3, hy, TileID.Tables, true, false, -1, 0);
            WorldGen.PlaceTile(hx + 6, hy, TileID.Chairs, true, false, -1, 0);
            WorldGen.PlaceTile(hx + 5, hy - 5, TileID.Torches, true, false, -1, 0);
            for (int x = hx - 1; x < hx + 13; x++) for (int y = hy - 8; y <= hy + 1; y++) { WorldGen.SquareTileFrame(x, y); WorldGen.SquareWallFrame(x, y); }
            yield return 5;
            d["house_door"] = Main.tile[hx + 11, hy - 1].HasTile ? (int)Main.tile[hx + 11, hy - 1].TileType : -1;
            d["house_table"] = Main.tile[hx + 3, hy].HasTile ? (int)Main.tile[hx + 3, hy].TileType : -1;
            d["house_chair"] = Main.tile[hx + 6, hy].HasTile ? (int)Main.tile[hx + 6, hy].TileType : -1;
            bool room = WorldGen.StartRoomCheck(hx + 5, hy - 2);
            d["room_check"] = room; d["room_fail"] = WorldGen.roomCheckFailureReason.ToString();
            d["room_needs"] = room && WorldGen.RoomNeeds(NPCID.Merchant);
            if (room) { WorldGen.ScoreRoom(-1, NPCID.Merchant); d["room_score"] = WorldGen.hiScore; }
            int before = Main.npc.Count(n => n.active && n.type == NPCID.Merchant);
            WorldGen.prioritizedTownNPCType = NPCID.Merchant; Main.townNPCCanSpawn[NPCID.Merchant] = true;
            var res = WorldGen.SpawnTownNPC(hx + 5, hy - 2);
            d["move_in"] = res.ToString();
            int mi = Array.FindIndex(Main.npc, n => n.active && n.type == NPCID.Merchant);
            d["merchant_home_in_house"] = mi >= 0 && !Main.npc[mi].homeless && Main.npc[mi].homeTileX >= hx && Main.npc[mi].homeTileX < hx + 12;
            d["merchants_before"] = before;
            yield return 30;
        }

        // ---- crafting: next to a work bench with 30 wood, the available recipes; craft a Wooden Sword (ingredients used)
        IEnumerable Craft()
        {
            var d = Cur;
            Stand(AX - 40); yield return 5;
            var keep = Me.inventory.Select(i => i.Clone()).ToArray();
            for (int i = 0; i < 58; i++) Me.inventory[i] = new Item();
            Me.inventory[20] = new Item(ItemID.Wood, 30);
            Me.AdjTiles(); Recipe.FindRecipes(); yield return 2;
            d["hand_recipes"] = Enumerable.Range(0, Main.numAvailableRecipes).Select(i => Main.recipe[Main.availableRecipe[i]].createItem.type).Distinct().OrderBy(x => x).ToList();
            WorldGen.PlaceTile(AX - 38, AY - 1, TileID.WorkBenches, true, false, -1, 0); WorldGen.SquareTileFrame(AX - 38, AY - 1); yield return 30;
            d["bench_tile"] = Main.tile[AX - 38, AY - 1].HasTile ? (int)Main.tile[AX - 38, AY - 1].TileType : -1;
            Me.AdjTiles(); Recipe.FindRecipes(); yield return 2;
            var avail = Enumerable.Range(0, Main.numAvailableRecipes).Select(i => Main.availableRecipe[i]).ToList();
            d["bench_near"] = Me.adjTile[TileID.WorkBenches];
            d["bench_recipes"] = avail.Select(i => Main.recipe[i].createItem.type).Distinct().OrderBy(x => x).ToList();
            int ri = avail.FirstOrDefault(i => Main.recipe[i].createItem.type == ItemID.WoodenSword && Main.recipe[i].Mod == null, -1);
            d["wooden_sword_recipe"] = ri >= 0;
            if (ri >= 0) { Main.recipe[ri].Create(); d["wood_left_after_sword"] = Me.inventory.Where(i => i.type == ItemID.Wood).Sum(i => i.stack); }
            for (int i = 0; i < keep.Length; i++) Me.inventory[i] = keep[i];
            WorldGen.KillTile(AX - 38, AY - 1, false, false, true); ClearItems(); Recipe.FindRecipes();
            yield return 10;
        }

        // ---- enemies: the game's spawner (NPC.SpawnNPC, 200 calls per tick) at the surface by day, by night and in the caverns; spawned types are counted
        IEnumerable Enemies()
        {
            var d = Cur;
            int sx = Main.spawnTileX, sy = Main.spawnTileY;
            // a cavern spot: first air pocket with floor below rockLayer + 40, near the spawn column
            int cx = -1, cy = -1;
            for (int dx = 0; dx < 300 && cx < 0; dx += 3)
                foreach (int x in new[] { sx + dx, sx - dx })
                    for (int y = (int)Main.rockLayer + 40; y < Main.maxTilesY - 250 && cx < 0; y++)
                        if (!Main.tile[x, y].HasTile && !Main.tile[x, y - 1].HasTile && !Main.tile[x, y - 2].HasTile && !Main.tile[x + 1, y].HasTile && !Main.tile[x + 1, y - 1].HasTile && !Main.tile[x + 1, y - 2].HasTile
                            && Main.tile[x, y + 1].HasTile && Main.tileSolid[Main.tile[x, y + 1].TileType] && Main.tile[x, y].LiquidAmount == 0) { cx = x; cy = y + 1; }
            d["cavern_spot"] = cx + "," + cy;
            foreach (var (name, day, x, y) in new[] { ("surface_day", true, sx, sy), ("surface_night", false, sx, sy), ("cavern", true, cx, cy) })
            {
                if (x < 0) { d[name] = "no spot"; continue; }
                Main.dayTime = day; Main.time = day ? 13500 : 16200;
                ClearNPCs(); Me.Teleport(new Vector2(x * 16 + 8 - Me.width / 2f, y * 16 - Me.height), -1); Me.velocity = Vector2.Zero; P.God = true;
                yield return 90;   // biome / scene scan
                P.Seed(500 + name.Length); P.Spawned.Clear(); P.CountSpawns = true;
                for (int t = 0; t < 150; t++)
                {
                    for (int k = 0; k < 200; k++)
                    {
                        NPC.SpawnNPC();
                        for (int i = 0; i < Main.maxNPCs; i++) if (Main.npc[i].active && !Main.npc[i].townNPC && !Main.npc[i].friendly && Main.npc[i].type != NPCID.OldMan) Main.npc[i].active = false;
                    }
                    yield return 1;
                }
                P.CountSpawns = false;
                d[name] = new Dictionary<string, int>(P.Spawned);
                d[name + "_zones"] = string.Join(",", new[] { Me.ZoneForest ? "forest" : null, Me.ZoneOverworldHeight ? "overworld" : null, Me.ZoneRockLayerHeight ? "rock" : null,
                    Me.ZoneDirtLayerHeight ? "dirt" : null, Me.ZoneCorrupt ? "corrupt" : null, Me.ZoneCrimson ? "crimson" : null, Me.ZoneDesert ? "desert" : null, Me.ZoneSnow ? "snow" : null,
                    Me.ZoneJungle ? "jungle" : null, Me.ZoneBeach ? "beach" : null }.Where(z => z != null));
                ClearNPCs();
            }
            Main.dayTime = true; Main.time = 13500; P.God = false;
            Stand(AX); yield return 30;
        }

        // ---- boss: Suspicious Looking Eye at night in the arena -> Eye of Cthulhu; stats, it charges, phase 2 below half life, loot, downedBoss1
        IEnumerable Boss()
        {
            var d = Cur;
            ClearNPCs(); Main.dayTime = false; Main.time = 3000; Stand(AX); P.God = true; yield return 30;
            Hold(ItemID.SuspiciousLookingEye, 1); P.Cursor = new Vector2(Me.Center.X + 50, Me.Center.Y);
            yield return 2; P.Use = true; yield return 2; P.Use = false;
            int bi = -1;
            for (int i = 0; i < 60 && bi < 0; i++) { yield return 1; bi = Array.FindIndex(Main.npc, n => n.active && n.type == NPCID.EyeofCthulhu); }
            d["spawned"] = bi >= 0; d["item_used"] = Me.inventory[9].IsAir;
            if (bi < 0) { P.God = false; Main.dayTime = true; Main.time = 13500; yield break; }
            var b = Main.npc[bi];
            d["life_max"] = b.lifeMax; d["damage"] = b.damage; d["defense"] = b.defense; d["boss"] = b.boss;
            double minDist = 1e9; int servants = 0; P.CountSpawns = true; P.Spawned.Clear();
            for (int i = 0; i < 600 && b.active; i++) { yield return 1; minDist = Math.Min(minDist, Vector2.Distance(b.Center, Me.Center)); }
            d["min_dist_10s"] = minDist < 60 ? "<60" : minDist < 200 ? "<200" : ">=200";
            b.life = (int)(b.lifeMax * 0.4); float maxAi0 = b.ai[0];
            for (int i = 0; i < 600 && b.active; i++) { yield return 1; maxAi0 = Math.Max(maxAi0, b.ai[0]); }
            servants = P.Spawned.TryGetValue("ServantofCthulhu", out var sc) ? sc : 0; P.CountSpawns = false;
            d["phase2"] = maxAi0 >= 1; d["servants_spawned"] = servants > 0;
            ClearItems(); P.WatchItems = true; P.NewItems.Clear(); P.Seed(77);
            if (b.active) b.StrikeInstantKill();
            yield return 20; P.WatchItems = false;
            d["loot_types"] = P.NewItems.Distinct().OrderBy(x => x).ToList();
            d["downed_boss1"] = NPC.downedBoss1;
            ClearNPCs(); ClearItems(); P.God = false; Main.dayTime = true; Main.time = 13500; Me.inventory[9] = new Item(); P.Cursor = null;
            yield return 30;
        }

        // ---- save: marker tile + marker item + world time, then Save & Quit (the run ends); the saveload run checks them
        IEnumerable Save()
        {
            Stand(AX); yield return 5;
            SetTile(AX + 5, AY - 10, TileID.GoldBrick);
            Me.inventory[40] = new Item(ItemID.Gel, 17);
            Me.statLife = 77;
            Main.dayTime = true; Main.time = 20000;
            P.Emit("save", new() { ["marker_tile"] = (AX + 5) + "," + (AY - 10), ["downed_boss1"] = NPC.downedBoss1, ["arena"] = AX + "," + AY });
            File.WriteAllText(Path.Combine(Path.GetDirectoryName(P.Out), "marker.txt"), (AX + 5) + "," + (AY - 10));
            P.Drive = false;
            WorldGen.SaveAndQuit(() => { P.Emit("_saved", new()); P.Emit("_done", new()); Main.instance.Exit(); });
            yield return 1;
        }

        IEnumerable SaveLoad()
        {
            var m = File.ReadAllText(Path.Combine(Path.GetDirectoryName(P.Out), "marker.txt")).Split(',').Select(int.Parse).ToArray();
            var t = Main.tile[m[0], m[1]];
            var it = Me.inventory[40];
            P.Emit("saveload", new()
            {
                ["marker_tile"] = t.HasTile ? (int)t.TileType : -1, ["marker_item"] = it.IsAir ? "none" : it.type + "x" + it.stack, ["life"] = Me.statLife,
                ["downed_boss1"] = NPC.downedBoss1, ["day"] = Main.dayTime, ["time_near_20000"] = Math.Abs(Main.time - 20000) < 600,
                ["merchant"] = Main.npc.Any(n => n.active && n.type == NPCID.Merchant), ["guide"] = Main.npc.Any(n => n.active && n.type == NPCID.Guide),
            });
            yield return 1;
        }
    }
}
