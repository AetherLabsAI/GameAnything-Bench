// GameAnything-Bench state bridge for Terraria (tModLoader 2026.08, Terraria 1.4.4.9): the live structured state of
// docs/STATE_SPEC.md for gab/game/terraria/adapter.py. A harness mod: it reads the running game and changes nothing in it.
// Inert unless the harness starts the game with one of:
//   GABSTATE_DIR=<dir>         client: answer state requests. The adapter writes <dir>/req (a request id); at the end of a
//                              frame (every frame is checked, menu and loading screens included) the bridge writes <dir>/ans
//                              (JSON {id, running, engine, mod, screen_text}). The one thing it sets: the window counts as focused
//                              (the virtual screen has no window manager; an unfocused single-player Terraria pauses its world)
//                              and does not sleep between frames when unfocused (as the GameAnything Terraria lab does).
//   GABSTATE_NEWPLAYER=<name>  dedicated server (-autocreate): create the start player like the character creation screen
//                              (vanilla start items + every loaded mod's start-inventory hooks), then write "GABSTATE done" to
//                              $GABSTATE_OUT; the runner stops the server.
// Mod data is read generically (STATE_SPEC "mod"): NPCs / projectiles / dropped items / tiles / walls / tile entities / buffs /
// items / biomes that mods added, the fields mods keep in their ModSystem / ModPlayer classes and static fields, open mod UIs.
// Screen text: chat lines, NPC dialog / sign, hover text, combat / popup texts, the world-generation status, the texts of mod
// UIs (UIText and the like) and text drawn by mod interface layers (Utils.DrawBorderString / ChatManager calls) in the last 500 ms.
using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text.Json;
using System.Text.RegularExpressions;
using Microsoft.Xna.Framework;
using Terraria;
using Terraria.DataStructures;
using Terraria.ID;
using Terraria.IO;
using Terraria.ModLoader;
using Terraria.UI;

namespace GABState
{
    public class GABState : Mod
    {
        public override void Load()
        {
            if (Main.dedServ || Br.Dir == null) return;
            Main.OnPostDraw += Br.Frame;
            Main.instance.InactiveSleepTime = TimeSpan.Zero;
            Br.AddHooks();
        }

        public override void Unload()
        {
            if (Br.Dir != null && !Main.dedServ) Main.OnPostDraw -= Br.Frame;
        }
    }

    public class GABStateSystem : ModSystem
    {
        public override void OnWorldLoad()
        {
            if (!Main.dedServ || string.IsNullOrEmpty(Br.NewPlayer)) return;
            string res;
            try { res = Br.CreatePlayer(Br.NewPlayer); } catch (Exception e) { res = "GABSTATE error " + e.ToString().Replace('\n', ' '); }
            try { File.AppendAllText(Br.Out, res + "\n"); } catch { }
            Mod.Logger.Info(res.Length > 300 ? res.Substring(0, 300) : res);
        }
    }

    public static class Br
    {
        public static readonly string Dir = Environment.GetEnvironmentVariable("GABSTATE_DIR") is string d && d.Length > 0 ? d : null;
        public static readonly string NewPlayer = Environment.GetEnvironmentVariable("GABSTATE_NEWPLAYER");
        public static readonly string Out = Environment.GetEnvironmentVariable("GABSTATE_OUT") ?? "/dev/null";
        static readonly HashSet<string> Harness = new() { "ModLoader", "GABState", "GAProbe" };
        static long frame;
        static string layer;                                   // the interface layer being drawn (On_GameInterfaceLayer.Draw)
        static readonly Dictionary<string, long> drawnAt = new();   // texts drawn by mod interface layers -> when (ms); "on screen" = drawn in the last 500 ms
        const int Cut = 200;

        // ------------------------------------------------------------ the server step: the start player
        public static string CreatePlayer(string name)
        {
            // what UICharacterCreation.SetupPlayerStatsAndInventoryBasedOnDifficulty does for a Classic character (as the pool
            // probe GAProbe.NewPlayer, verified 2026-10-30), with Main.rand seeded so the start items' prefixes are the same every time
            Main.rand = new Terraria.Utilities.UnifiedRandom(7);
            var p = new Player { name = name, difficulty = 0 };
            int n = 0;
            p.inventory[n].SetDefaults(ItemID.CopperShortsword); p.inventory[n++].Prefix(-1);
            p.inventory[n].SetDefaults(ItemID.CopperPickaxe); p.inventory[n++].Prefix(-1);
            p.inventory[n].SetDefaults(ItemID.CopperAxe); p.inventory[n++].Prefix(-1);
            p.savedPerPlayerFieldsThatArentInThePlayerClass = new Player.SavedPlayerDataWithAnnoyingRules();
            var vanilla = p.inventory.Where(i => !i.IsAir).Select(i => i.Clone());
            PlayerLoader.SetStartInventory(p, PlayerLoader.GetStartingItems(p, vanilla));
            Directory.CreateDirectory(Main.PlayerPath);
            var fd = new PlayerFileData(Main.GetPlayerPathFromName(p.name, false), false) { Metadata = FileMetadata.FromCurrentSettings(FileType.Player), Player = p };
            typeof(Player).GetMethod("InternalSavePlayerFile", BindingFlags.NonPublic | BindingFlags.Static).Invoke(null, new object[] { fd });
            return "GABSTATE done " + fd.Path + " items " + string.Join(",", p.inventory.Where(i => !i.IsAir).Select(i => i.type + "x" + i.stack));
        }

        // ------------------------------------------------------------ text drawn by mod interface layers
        public static void AddHooks()
        {
            On_GameInterfaceLayer.Draw += (orig, self) =>
            {
                var prev = layer; layer = self.Name;
                try { return orig(self); } finally { layer = prev; }
            };
            Terraria.On_Utils.DrawBorderString += (orig, sb, text, pos, color, scale, ax, ay, max) => { Seen(text); return orig(sb, text, pos, color, scale, ax, ay, max); };
            Terraria.On_Utils.DrawBorderStringBig += (orig, sb, text, pos, color, scale, ax, ay, max) => { Seen(text); return orig(sb, text, pos, color, scale, ax, ay, max); };
            Terraria.UI.Chat.On_ChatManager.DrawColorCodedStringWithShadow_SpriteBatch_DynamicSpriteFont_string_Vector2_Color_float_Vector2_Vector2_float_float +=
                (orig, sb, font, text, pos, color, rot, origin, scale, maxW, spread) => { Seen(text); return orig(sb, font, text, pos, color, rot, origin, scale, maxW, spread); };
            Terraria.UI.Chat.On_ChatManager.DrawColorCodedStringWithShadow_SpriteBatch_DynamicSpriteFont_string_Vector2_Color_Color_float_Vector2_Vector2_float_float +=
                (orig, sb, font, text, pos, color, shadow, rot, origin, scale, maxW, spread) => { Seen(text); return orig(sb, font, text, pos, color, shadow, rot, origin, scale, maxW, spread); };
            // the lower level every text helper ends in (DrawBorderString -> DrawBorderStringFourWay / the snippet DrawColorCodedString)
            Terraria.On_Utils.DrawBorderStringFourWay += (orig, sb, font, text, x, y, c, bc, origin, scale) => { Seen(text); orig(sb, font, text, x, y, c, bc, origin, scale); };
            Terraria.UI.Chat.On_ChatManager.DrawColorCodedString_SpriteBatch_DynamicSpriteFont_TextSnippetArray_Vector2_Color_float_Vector2_Vector2_refInt32_float_bool +=
                (Terraria.UI.Chat.On_ChatManager.orig_DrawColorCodedString_SpriteBatch_DynamicSpriteFont_TextSnippetArray_Vector2_Color_float_Vector2_Vector2_refInt32_float_bool orig,
                 Microsoft.Xna.Framework.Graphics.SpriteBatch sb, ReLogic.Graphics.DynamicSpriteFont font, Terraria.UI.Chat.TextSnippet[] snippets, Vector2 pos, Color color,
                 float rot, Vector2 origin, Vector2 scale, out int hovered, float maxW, bool ignoreColors) =>
                {
                    if (layer != null && snippets != null) Seen(string.Concat(snippets.Select(x => x?.Text)));
                    return orig(sb, font, snippets, pos, color, rot, origin, scale, out hovered, maxW, ignoreColors);
                };
            Terraria.UI.Chat.On_ChatManager.DrawColorCodedString_SpriteBatch_DynamicSpriteFont_string_Vector2_Color_float_Vector2_Vector2_float_bool +=
                (orig, sb, font, text, pos, color, rot, origin, scale, maxW, ignoreColors) => { Seen(text); return orig(sb, font, text, pos, color, rot, origin, scale, maxW, ignoreColors); };
        }

        static void Seen(string text)
        {
            if (layer == null || layer.StartsWith("Vanilla:") || string.IsNullOrWhiteSpace(text)) return;
            long now = Environment.TickCount64;
            if (drawnAt.Count > 200) foreach (var k in drawnAt.Where(kv => now - kv.Value > 2000).Select(kv => kv.Key).ToList()) drawnAt.Remove(k);
            if (drawnAt.Count <= 200) drawnAt[Clean(text)] = now;
        }

        // ------------------------------------------------------------ requests (end of every frame, main thread)
        public static void Frame(GameTime gt)
        {
            frame++;
            if (!Main.instance.IsActive || !Main.hasFocus) Focus();
            string req = Path.Combine(Dir, "req");
            if (!File.Exists(req)) return;
            string id;
            try { id = File.ReadAllText(req).Trim(); File.Delete(req); } catch { return; }
            string json;
            try { json = JsonSerializer.Serialize(Snapshot(id)); }
            catch (Exception e) { json = JsonSerializer.Serialize(new Dictionary<string, object> { ["id"] = id, ["running"] = true, ["error"] = e.GetType().Name + ": " + e.Message }); }
            try { string tmp = Path.Combine(Dir, "ans.tmp"); File.WriteAllText(tmp, json); File.Move(tmp, Path.Combine(Dir, "ans"), true); } catch { }
        }

        static void Focus()
        {
            try { typeof(Microsoft.Xna.Framework.Game).GetProperty("IsActive")?.GetSetMethod(true)?.Invoke(Main.instance, new object[] { true }); } catch { }
            Main.hasFocus = true;
        }

        static Dictionary<string, object> Snapshot(string id) => new()
        {
            ["id"] = id, ["running"] = true, ["frame"] = frame, ["engine"] = Engine(), ["mod"] = ModData(), ["screen_text"] = ScreenText(),
        };

        // ------------------------------------------------------------ engine
        static double R(double v, int n = 1) => Math.Round(v, n);
        static List<double> T(Vector2 worldPos) => new() { R(worldPos.X / 16.0), R(worldPos.Y / 16.0) };
        static string Clean(string s)
        {
            if (s == null) return null;
            s = Regex.Replace(s, @"\[c/[0-9A-Fa-f]{6}:([^\]]*)\]", "$1");
            s = Regex.Replace(s, @"\[(?:i|item)(?:/[^:\]]*)?:(\d+)\]", m => int.TryParse(m.Groups[1].Value, out var t) && t > 0 && t < ItemLoader.ItemCount ? Lang.GetItemNameValue(t) : m.Value);
            s = s.Replace('\n', ' ').Trim();
            return s.Length > Cut ? s.Substring(0, Cut) : s;
        }
        static bool IsHarness(Mod m) => m == null || Harness.Contains(m.Name);
        static string ModKey(ModType t) => t.Mod.Name + "/" + t.Name;

        static Dictionary<string, object> Engine()
        {
            var e = new Dictionary<string, object>
            {
                ["screen"] = null, ["menus_open"] = new List<string>(), ["menu_mode"] = null, ["world"] = null, ["time"] = null, ["player"] = null,
                ["progress"] = null, ["npcs"] = new Dictionary<string, object>(), ["bosses"] = new List<string>(),
                ["mods"] = ModLoader.Mods.Where(m => !IsHarness(m)).Select(m => m.Name + " " + m.Version).ToList(),
            };
            if (Main.gameMenu)
            {
                e["screen"] = string.IsNullOrEmpty(Main.statusText) ? "menu" : "loading";
                e["menu_mode"] = Main.menuMode;
                return e;
            }
            var p = Main.LocalPlayer;
            var open = (List<string>)e["menus_open"];
            if (Main.playerInventory) open.Add("inventory");
            if (p.chest != -1) open.Add("chest");
            if (p.talkNPC >= 0) open.Add("npc_chat");
            if (p.sign >= 0) open.Add("sign");
            if (Main.mapFullscreen) open.Add("map");
            if (Main.ingameOptionsWindow) open.Add("options");
            if (Main.InGameUI?.CurrentState != null) open.Add("ui:" + Main.InGameUI.CurrentState.GetType().Name);
            if (Main.drawingPlayerChat) open.Add("chat_input");
            e["screen"] = p.dead ? "dead" : Main.gamePaused ? "paused" : "in_game";
            var wf = Main.ActiveWorldFileData;
            e["world"] = new Dictionary<string, object>
            {
                ["name"] = Main.worldName, ["seed"] = wf?.SeedText, ["size"] = new List<int> { Main.maxTilesX, Main.maxTilesY },
                ["evil"] = WorldGen.crimson ? "crimson" : "corruption", ["difficulty"] = Main.GameMode, ["hardmode"] = Main.hardMode,
                ["spawn"] = new List<int> { Main.spawnTileX, Main.spawnTileY },
            };
            double secs = (Main.dayTime ? 4.5 * 3600 : 19.5 * 3600) + Main.time;
            int hh = (int)(secs / 3600) % 24, mm = (int)(secs % 3600 / 60);
            var ev = new List<string>();
            if (Main.bloodMoon) ev.Add("blood_moon"); if (Main.eclipse) ev.Add("eclipse"); if (Main.raining) ev.Add("rain");
            if (Main.slimeRain) ev.Add("slime_rain"); if (Main.pumpkinMoon) ev.Add("pumpkin_moon"); if (Main.snowMoon) ev.Add("frost_moon");
            if (Main.invasionType > 0) ev.Add("invasion " + Main.invasionType);
            e["time"] = new Dictionary<string, object> { ["day"] = Main.dayTime, ["clock"] = $"{hh}:{mm:00}", ["ticks"] = (int)Main.time, ["moon_phase"] = Main.moonPhase, ["events"] = ev };
            var inv = new Dictionary<string, object>();
            for (int i = 0; i < 58; i++)
            {
                var it = p.inventory[i]; if (it.IsAir) continue;
                string k = it.Name; inv[k] = (inv.TryGetValue(k, out var c) ? (int)c : 0) + it.stack;
                if (inv.Count >= 30) break;
            }
            var zones = typeof(Player).GetProperties(BindingFlags.Public | BindingFlags.Instance)
                .Where(pr => pr.PropertyType == typeof(bool) && pr.Name.StartsWith("Zone") && pr.GetIndexParameters().Length == 0)
                .Where(pr => { try { return (bool)pr.GetValue(p); } catch { return false; } }).Select(pr => pr.Name.Substring(4)).OrderBy(x => x).ToList();
            e["player"] = new Dictionary<string, object>
            {
                ["name"] = p.name, ["pos"] = T(p.Center), ["velocity"] = new List<double> { R(p.velocity.X, 2), R(p.velocity.Y, 2) },
                ["on_ground"] = p.velocity.Y == 0f, ["life"] = p.statLife, ["life_max"] = p.statLifeMax2, ["mana"] = p.statMana, ["mana_max"] = p.statManaMax2,
                ["defense"] = (int)p.statDefense, ["dead"] = p.dead, ["held"] = p.HeldItem?.IsAir == false ? p.HeldItem.Name : null, ["selected_slot"] = p.selectedItem,
                ["coins"] = Utils.CoinsCount(out _, p.inventory, new[] { 58 }), ["inventory"] = inv,
                ["armor"] = p.armor.Take(3).Where(i => !i.IsAir).Select(i => i.Name).ToList(),
                ["accessories"] = p.armor.Skip(3).Take(7).Where(i => !i.IsAir).Select(i => i.Name).ToList(),
                ["buffs"] = p.buffType.Where((b, k) => b > 0 && p.buffTime[k] > 0).Select(b => Lang.GetBuffName(b)).ToList(),
                ["zones"] = zones,
            };
            var downed = new List<string>();
            void D(bool f, string n) { if (f) downed.Add(n); }
            D(NPC.downedSlimeKing, "king_slime"); D(NPC.downedBoss1, "eye_of_cthulhu"); D(NPC.downedBoss2, "evil_boss"); D(NPC.downedQueenBee, "queen_bee");
            D(NPC.downedBoss3, "skeletron"); D(NPC.downedDeerclops, "deerclops"); D(Main.hardMode, "wall_of_flesh"); D(NPC.downedQueenSlime, "queen_slime");
            D(NPC.downedMechBossAny, "a_mech_boss"); D(NPC.downedPlantBoss, "plantera"); D(NPC.downedGolemBoss, "golem"); D(NPC.downedFishron, "duke_fishron");
            D(NPC.downedEmpressOfLight, "empress_of_light"); D(NPC.downedAncientCultist, "lunatic_cultist"); D(NPC.downedMoonlord, "moon_lord");
            D(NPC.downedGoblins, "goblin_army"); D(NPC.downedPirates, "pirates"); D(NPC.downedFrost, "frost_legion"); D(NPC.downedMartians, "martians");
            e["progress"] = new Dictionary<string, object> { ["downed"] = downed };
            var near = new List<(float, NPC)>();
            for (int i = 0; i < Main.maxNPCs; i++) { var n = Main.npc[i]; if (n.active && n.ModNPC == null) near.Add((Vector2.Distance(n.Center, p.Center), n)); }
            var npcs = (Dictionary<string, object>)e["npcs"];
            foreach (var (dist, n) in near.OrderBy(x => x.Item1).Take(12))
                npcs[n.TypeName + "#" + n.whoAmI] = new Dictionary<string, object> { ["pos"] = T(n.Center), ["dist"] = R(dist / 16.0), ["life"] = n.life, ["life_max"] = n.lifeMax,
                    ["town"] = n.townNPC, ["friendly"] = n.friendly };
            e["bosses"] = Enumerable.Range(0, Main.maxNPCs).Select(i => Main.npc[i]).Where(n => n.active && n.boss).Select(n => n.TypeName + " " + n.life + "/" + n.lifeMax).Take(5).ToList();
            return e;
        }

        // ------------------------------------------------------------ mod data
        static Dictionary<string, object> ModData()
        {
            var m = new Dictionary<string, object>();
            var vars = Vars();
            if (vars.Count > 0) m["vars"] = vars;
            var ui = ModUIs().Select(u => u.GetType().FullName).Distinct().ToList();
            if (ui.Count > 0) m["ui"] = ui;
            if (Main.gameMenu) return m;
            var p = Main.LocalPlayer; var c = p.Center;
            var npcs = new Dictionary<string, object>();
            foreach (var n in Enumerable.Range(0, Main.maxNPCs).Select(i => Main.npc[i]).Where(n => n.active && n.ModNPC != null && !IsHarness(n.ModNPC.Mod))
                         .OrderBy(n => Vector2.Distance(n.Center, c)).Take(20))
                npcs[ModKey(n.ModNPC) + "#" + n.whoAmI] = new Dictionary<string, object> { ["name"] = n.GivenOrTypeName, ["pos"] = T(n.Center), ["dist"] = R(Vector2.Distance(n.Center, c) / 16.0),
                    ["life"] = n.life, ["life_max"] = n.lifeMax, ["town"] = n.townNPC, ["friendly"] = n.friendly, ["boss"] = n.boss };
            if (npcs.Count > 0) m["npcs"] = npcs;
            var proj = new Dictionary<string, object>();
            foreach (var q in Main.projectile.Where(q => q.active && q.ModProjectile != null && !IsHarness(q.ModProjectile.Mod)).OrderBy(q => Vector2.Distance(q.Center, c)).Take(10))
                proj[ModKey(q.ModProjectile) + "#" + q.whoAmI] = new Dictionary<string, object> { ["pos"] = T(q.Center), ["owner"] = q.owner, ["hostile"] = q.hostile };
            if (proj.Count > 0) m["projectiles"] = proj;
            var drops = new Dictionary<string, object>();
            foreach (var it in Main.item.Where(it => it.active && it.ModItem != null && !IsHarness(it.ModItem.Mod)).OrderBy(it => Vector2.Distance(it.Center, c)).Take(10))
                drops[ModKey(it.ModItem) + "#" + it.whoAmI] = new Dictionary<string, object> { ["name"] = it.Name, ["stack"] = it.stack, ["pos"] = T(it.Center) };
            if (drops.Count > 0) m["items_dropped"] = drops;
            var inv = new Dictionary<string, object>();
            foreach (var it in p.inventory.Concat(p.armor).Concat(p.miscEquips).Where(it => !it.IsAir && it.ModItem != null && !IsHarness(it.ModItem.Mod)))
            {
                string k = ModKey(it.ModItem);
                int have = inv.TryGetValue(k, out var o) ? (int)((Dictionary<string, object>)o)["count"] : 0;
                inv[k] = new Dictionary<string, object> { ["name"] = it.Name, ["count"] = have + it.stack };
            }
            if (inv.Count > 0) m["inventory"] = inv;
            var buffs = p.buffType.Where((b, k) => b >= BuffID.Count && p.buffTime[k] > 0).Select(b => BuffLoader.GetBuff(b)).Where(b => b != null && !IsHarness(b.Mod)).Select(b => ModKey(b)).ToList();
            if (buffs.Count > 0) m["buffs"] = buffs;
            var biomes = ModContent.GetContent<ModBiome>().Where(b => !IsHarness(b.Mod) && p.InModBiome(b)).Select(b => ModKey(b)).ToList();
            if (biomes.Count > 0) m["biomes"] = biomes;
            // tiles and walls of mods around the player (120 x 70 tiles, about two screens)
            var tiles = new Dictionary<string, (int n, int x, int y, int d)>(); var walls = new Dictionary<string, (int n, int x, int y, int d)>();
            int px = (int)(c.X / 16), py = (int)(c.Y / 16);
            for (int x = Math.Max(0, px - 60); x < Math.Min(Main.maxTilesX, px + 60); x++)
                for (int y = Math.Max(0, py - 35); y < Math.Min(Main.maxTilesY, py + 35); y++)
                {
                    var t = Main.tile[x, y]; int d = Math.Abs(x - px) + Math.Abs(y - py);
                    if (t.HasTile && t.TileType >= TileID.Count && TileLoader.GetTile(t.TileType) is ModTile mt && !IsHarness(mt.Mod)) Acc(tiles, ModKey(mt), x, y, d);
                    if (t.WallType >= WallID.Count && WallLoader.GetWall(t.WallType) is ModWall mw && !IsHarness(mw.Mod)) Acc(walls, ModKey(mw), x, y, d);
                }
            if (tiles.Count > 0) m["tiles"] = tiles.OrderBy(k => k.Value.d).Take(20).ToDictionary(k => k.Key, k => (object)new Dictionary<string, object> { ["count"] = k.Value.n, ["nearest"] = new List<int> { k.Value.x, k.Value.y } });
            if (walls.Count > 0) m["walls"] = walls.OrderBy(k => k.Value.d).Take(10).ToDictionary(k => k.Key, k => (object)new Dictionary<string, object> { ["count"] = k.Value.n, ["nearest"] = new List<int> { k.Value.x, k.Value.y } });
            var tes = new Dictionary<string, object>();
            foreach (var te in TileEntity.ByID.Values.OfType<ModTileEntity>().Where(te => !IsHarness(te.Mod)).OrderBy(te => Math.Abs(te.Position.X - px) + Math.Abs(te.Position.Y - py)).Take(10))
            {
                var f = new Dictionary<string, object>(); Fields(te, te.GetType(), te.Mod, "", f, false, 8);
                tes[te.Mod.Name + "/" + te.Name + "@" + te.Position.X + "," + te.Position.Y] = f;
            }
            if (tes.Count > 0) m["tile_entities"] = tes;
            return m;
        }

        static void Acc(Dictionary<string, (int n, int x, int y, int d)> into, string k, int x, int y, int d)
        {
            if (into.TryGetValue(k, out var v)) into[k] = (v.n + 1, d < v.d ? x : v.x, d < v.d ? y : v.y, Math.Min(d, v.d));
            else into[k] = (1, x, y, d);
        }

        // the fields mods keep: ModSystem and (local) ModPlayer instances, static fields of the mod's own types (not readonly / const)
        static Dictionary<string, Type[]> typesOf = new();
        static Dictionary<string, object> Vars()
        {
            var v = new Dictionary<string, object>();
            foreach (var mod in ModLoader.Mods.Where(m => !IsHarness(m) && m.Code != null))
            {
                if (!Main.gameMenu)
                    foreach (var mp in Main.LocalPlayer.ModPlayers) if (mp.Mod == mod) Fields(mp, mp.GetType(), mod, mod.Name + "." + mp.GetType().Name + ".", v, false, 40);
                foreach (var sys in ModContent.GetContent<ModSystem>().Where(s => s.Mod == mod)) Fields(sys, sys.GetType(), mod, mod.Name + "." + sys.GetType().Name + ".", v, false, 40);
                if (!typesOf.TryGetValue(mod.Name, out var ts))
                {
                    try { ts = mod.Code.GetTypes(); } catch (ReflectionTypeLoadException e) { ts = e.Types.Where(t => t != null).ToArray(); }
                    typesOf[mod.Name] = ts = ts.Where(t => !t.IsGenericTypeDefinition && !t.Name.Contains('<')).ToArray();
                }
                foreach (var t in ts) Fields(null, t, mod, mod.Name + "." + t.Name + ".", v, true, 40);
                if (v.Count >= 40) break;
            }
            return v;
        }

        static void Fields(object inst, Type type, Mod mod, string prefix, Dictionary<string, object> into, bool statics, int cap)
        {
            for (var t = type; t != null && t.Assembly == mod.Code; t = t.BaseType)
            {
                FieldInfo[] fs;
                try { fs = t.GetFields(BindingFlags.DeclaredOnly | BindingFlags.Public | BindingFlags.NonPublic | (statics ? BindingFlags.Static : BindingFlags.Instance)); }
                catch { continue; }
                foreach (var f in fs.OrderBy(f => f.Name))
                {
                    if (into.Count >= cap) return;
                    if (f.IsLiteral || (statics && f.IsInitOnly)) continue;
                    string name = f.Name;
                    if (name.StartsWith("<")) { if (!name.EndsWith(">k__BackingField")) continue; name = name.Substring(1, name.IndexOf('>') - 1); }
                    object val;
                    try { val = Simple(f.GetValue(inst), 0); } catch { continue; }
                    if (val is NotSimple) continue;
                    into[prefix + name] = val;
                }
                if (statics) break;
            }
        }

        sealed class NotSimple { public static readonly NotSimple I = new(); }
        static object Simple(object v, int depth)
        {
            switch (v)
            {
                case null: return null;
                case bool or int or long or short or byte or uint or ushort or sbyte: return v;
                case float f: return Math.Round(f, 3);
                case double d: return Math.Round(d, 3);
                case string s: return s.Length > Cut ? s.Substring(0, Cut) : s;
                case Enum en: return en.ToString();
                case Vector2 v2: return new List<double> { Math.Round(v2.X, 2), Math.Round(v2.Y, 2) };
                case Point pt: return new List<int> { pt.X, pt.Y };
                case Point16 p16: return new List<int> { p16.X, p16.Y };
            }
            if (depth > 0) return NotSimple.I;
            if (v is IDictionary dict)
            {
                var o = new Dictionary<string, object>();
                foreach (DictionaryEntry kv in dict) { var x = Simple(kv.Value, 1); if (x is NotSimple) return NotSimple.I; o[Convert.ToString(kv.Key)] = x; if (o.Count >= 10) break; }
                return o;
            }
            if (v is IEnumerable en2)
            {
                var l = new List<object>();
                foreach (var it in en2) { var x = Simple(it, 1); if (x is NotSimple) return NotSimple.I; l.Add(x); if (l.Count >= 10) break; }
                return l;
            }
            return NotSimple.I;
        }

        static IEnumerable<UIState> ModUIs()
        {
            var seen = new HashSet<object>();
            foreach (var sys in ModContent.GetContent<ModSystem>().Where(s => !IsHarness(s.Mod)))
                foreach (var f in sys.GetType().GetFields(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.Static))
                {
                    object val; try { val = f.GetValue(f.IsStatic ? null : sys); } catch { continue; }
                    if (val is UserInterface ui && ui.CurrentState != null && seen.Add(ui.CurrentState)) yield return ui.CurrentState;
                }
            foreach (var host in new[] { Main.InGameUI, Main.MenuUI })
            {
                var st = host?.CurrentState;
                if (st != null && ModLoader.Mods.Any(m => !IsHarness(m) && m.Code == st.GetType().Assembly) && seen.Add(st)) yield return st;
            }
        }

        static void UITexts(UIElement el, List<string> into, int depth)
        {
            if (el == null || depth > 12 || into.Count >= 20) return;
            try
            {
                var pr = el.GetType().GetProperty("Text", BindingFlags.Public | BindingFlags.Instance);
                if (pr != null && pr.PropertyType == typeof(string) && pr.GetValue(el) is string s && !string.IsNullOrWhiteSpace(s)) into.Add(Clean(s));
            }
            catch { }
            foreach (var ch in el.Children) UITexts(ch, into, depth + 1);
        }

        // ------------------------------------------------------------ screen text
        static readonly FieldInfo chatMessages = Main.chatMonitor?.GetType().GetField("_messages", BindingFlags.NonPublic | BindingFlags.Instance);
        static List<string> ScreenText()
        {
            var o = new List<string>();
            void Add(string prefix, string s) { s = Clean(s); if (!string.IsNullOrWhiteSpace(s) && o.Count < 30 && !o.Contains(prefix + s)) o.Add(prefix + s); }
            if (Main.gameMenu) Add("status: ", Main.statusText);
            else
            {
                var p = Main.LocalPlayer;
                if (p.talkNPC >= 0) Add("dialog: " + Main.npc[p.talkNPC].GivenOrTypeName + ": ", Main.npcChatText);
                if (p.sign >= 0) Add("sign: ", Main.npcChatText);
                foreach (var ct in Main.combatText.Where(ct => ct.active && !Regex.IsMatch(ct.text ?? "", @"^\d+$")).Take(5)) Add("combat: ", ct.text);
                foreach (var pt in Main.popupText.Where(pt => pt.active).Take(3)) Add("popup: ", pt.name + (pt.stack > 1 ? " (" + pt.stack + ")" : ""));
                if (!string.IsNullOrEmpty(Main.hoverItemName)) Add("hover: ", Main.hoverItemName);
            }
            try
            {
                if (chatMessages?.GetValue(Main.chatMonitor) is IList msgs)
                {
                    var lines = new List<string>();
                    foreach (var m in msgs) if (m is Terraria.UI.Chat.ChatMessageContainer c && !string.IsNullOrWhiteSpace(c.OriginalText)) lines.Add(c.OriginalText);
                    foreach (var l in lines.Take(8).Reverse()) Add("chat: ", l);    // newest first in the monitor's list
                }
            }
            catch { }
            foreach (var st in ModUIs()) { var l = new List<string>(); UITexts(st, l, 0); foreach (var s in l) Add("ui: ", s); }
            long t = Environment.TickCount64;
            foreach (var s in drawnAt.Where(kv => t - kv.Value < 500).Select(kv => kv.Key).OrderBy(x => x).ToList()) Add("hud: ", s);
            return o;
        }
    }
}
