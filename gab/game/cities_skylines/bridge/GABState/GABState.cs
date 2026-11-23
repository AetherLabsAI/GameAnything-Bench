// GameAnything-Bench state bridge for Cities: Skylines 1 (ICities mod, .NET 3.5, C# 7.3): the live structured state of docs/STATE_SPEC.md
// for gab/game/cities_skylines (adapter pending: the pool verification comes first). A harness mod: it reads the running game and
// changes nothing. Inert unless the folder $XDG_DATA_HOME/gabstate exists (the adapter makes it, a link to local disk; the start script
// passes XDG_DATA_HOME through its minimal environment): the adapter writes gabstate/req (a request id), the bridge answers at the end
// of the next frame (menus and loading included) with gabstate/ans = JSON {id, running, engine, mod, screen_text}.
// engine: status (menu | loading | in_game | other), city, map, date, paused, speed, money, population, happiness, demand {res, com, wrk},
//   counts {buildings, segments, vehicles, citizens}, camera {x, z, height, size, angle}, tool (current tool class), mods (enabled
//   non-builtin plugins), time_of_day {hour, night, day_night_cycle}, transport {lines, by_type {Bus: n}, vehicles, list [{name, type,
//   stops, vehicles}] (first 10)} (2026-11-05: the Cities tasks ask for lighting over the day and bus lines; written without the game,
//   UNTESTED: members not checked against the assemblies are read by reflection, a wrong guess gives null, never a compile error).
// mod: buildings / networks / props by prefab that is custom content or not in the stock prefab list (<dir>/stock_prefabs.txt, written
//   by a vanilla run: request "dump"), districts with names, static fields of the mods' assemblies (simple values, 40 max).
// screen_text: Chirper messages (MessageManager recent), the current message panel, visible UILabels of mod-made UI components.
// Start (the harness's menu-less start, like the Valheim bridge's session): gabstate/start.cfg = "new:<map>|<city>" or "load:<save>";
// 4 s after the main menu is up the bridge loads it once per game process -- never past a legal / terms dialog: then it writes
// gabstate/blocked (the dialog and the unread documents) and does nothing (the user accepts those herself).
using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Text;
using ColossalFramework;
using ColossalFramework.Packaging;
using ColossalFramework.PlatformServices;
using ColossalFramework.Plugins;
using ColossalFramework.UI;
using ICities;
using UnityEngine;

namespace GABState
{
    public class Mod : IUserMod
    {
        public string Name { get { return "GABState"; } }
        public string Description { get { return "GameAnything-Bench harness: reads the running game's state (changes nothing)"; } }
        public void OnEnabled() { Host.Ensure(); }
    }

    public class Host : MonoBehaviour
    {
        static Host inst;
        static string dir;
        static long frame;
        static bool started; static float menuSince = -1;
        static HashSet<string> stock;
        static readonly HashSet<string> Harness = new HashSet<string> { "GABState", "CSProbe", "CitiesBridge" };

        public static void Ensure()
        {
            if (inst != null) return;
            var x = Environment.GetEnvironmentVariable("XDG_DATA_HOME");
            if (string.IsNullOrEmpty(x) || !Directory.Exists(Path.Combine(x, "gabstate"))) return;
            dir = Path.Combine(x, "gabstate");
            var go = new GameObject("GABStateHost"); DontDestroyOnLoad(go); inst = go.AddComponent<Host>();
        }

        void Update()
        {
            frame++;
            try { AutoStart(); } catch (Exception e) { try { File.WriteAllText(Path.Combine(dir, "blocked"), "start: " + e); } catch { } started = true; }
            if (frame % 3 != 0) return;
            string req = Path.Combine(dir, "req");
            if (!File.Exists(req)) return;
            string id;
            try { id = File.ReadAllText(req).Trim(); File.Delete(req); } catch { return; }
            string json;
            try
            {
                if (id.EndsWith(":dump")) { DumpStock(); id = id.Substring(0, id.Length - 5); }
                json = J(Snapshot(id));
            }
            catch (Exception e) { json = J(D("id", id, "running", true, "error", e.GetType().Name + ": " + e.Message)); }
            try { var tmp = Path.Combine(dir, "ans.tmp"); File.WriteAllText(tmp, json); var ans = Path.Combine(dir, "ans"); if (File.Exists(ans)) File.Delete(ans); File.Move(tmp, ans); }
            catch { }
        }

        // ------------------------------------------------------------ the start
        static void AutoStart()
        {
            if (started) return;
            if (Status() == "menu") { if (menuSince < 0) menuSince = Time.realtimeSinceStartup; } else { menuSince = -1; return; }
            if (Time.realtimeSinceStartup - menuSince < 4f) return;
            var cfg = Path.Combine(dir, "start.cfg");
            if (!File.Exists(cfg)) return;
            started = true;
            foreach (var n in new[] { "LegalDisclaimerPanel", "DisclaimerPanel" })
            {
                UIComponent c = null; try { c = UIView.library.Get(n); } catch { }
                if (c != null && c.isVisible)
                {
                    var docs = new List<string>();
                    try { foreach (var dd in Singleton<PopsManager>.instance.UnreadLegalDocuments) docs.Add("legal." + dd.Type + "." + dd.Title); } catch { }
                    File.WriteAllText(Path.Combine(dir, "blocked"), "DIALOG " + n + " is showing: not clicked, not started; unread: " + string.Join(", ", docs.ToArray()));
                    return;
                }
            }
            var start = File.ReadAllText(cfg).Trim();
            var colon = start.IndexOf(':');
            string kind = colon > 0 ? start.Substring(0, colon) : "new", arg = colon > 0 ? start.Substring(colon + 1) : start;
            foreach (var n in new[] { "NewGamePanel", "LoadPanel", "ContentManagerPanel", "OptionsPanel" }) try { UIView.library.Hide(n); } catch { }
            if (kind == "new")
            {
                var parts = arg.Split('|'); var map = parts[0]; var cityName = parts.Length > 1 ? parts[1] : "GAB City";
                Package.Asset asset = null; MapMetaData md = null; var seen = new List<string>();
                foreach (var a in PackageManager.FilterAssets(UserAssetType.MapMetaData))
                {
                    if (a == null || !a.isEnabled) continue;
                    var m = a.Instantiate<MapMetaData>(); if (m == null) continue;
                    seen.Add(m.mapName);
                    if (m.mapName == map || a.name == map) { asset = a; md = m; break; }
                }
                if (asset == null) { File.WriteAllText(Path.Combine(dir, "blocked"), "no map '" + map + "'; maps: " + string.Join(", ", seen.ToArray())); return; }
                var meta = new SimulationMetaData
                {
                    m_CityName = cityName, m_gameInstanceIdentifier = Guid.NewGuid().ToString(), m_invertTraffic = SimulationMetaData.MetaBool.False,
                    m_disableAchievements = SimulationMetaData.MetaBool.True, m_startingDateTime = DateTime.Now, m_currentDateTime = DateTime.Now,
                    m_newGameAppVersion = 222554386u, m_updateMode = SimulationManager.UpdateMode.NewGameFromMap, m_MapName = md.mapName
                };
                if (md.mapThemeRef != null)
                {
                    var th = PackageManager.FindAssetByName(md.mapThemeRef, UserAssetType.MapThemeMetaData);
                    if (th != null) { meta.m_MapThemeMetaData = th.Instantiate<MapThemeMetaData>(); meta.m_MapThemeMetaData.SetSelfRef(th); }
                }
                // the data asset the metadata points to (what NewGamePanel / LoadPanel pass); the metadata asset itself is read as simulation
                // data and fails ("File format version not supported (5459297 > 121034)")
                Singleton<LoadingManager>.instance.LoadLevel(md.assetRef ?? asset, "Game", "InGame", meta);
            }
            else
            {
                Package.Asset asset = null; SaveGameMetaData sd = null; var seen = new List<string>();
                foreach (var a in PackageManager.FilterAssets(UserAssetType.SaveGameMetaData))
                {
                    if (a == null || !a.isEnabled) continue;
                    seen.Add(a.name);
                    if (a.name == arg) { asset = a; sd = a.Instantiate<SaveGameMetaData>(); break; }
                }
                if (asset == null || sd == null) { File.WriteAllText(Path.Combine(dir, "blocked"), "no save '" + arg + "'; saves: " + string.Join(", ", seen.ToArray())); return; }
                var meta = new SimulationMetaData { m_CityName = sd.cityName, m_updateMode = SimulationManager.UpdateMode.LoadGame };
                if (sd.mapThemeRef != null)
                {
                    var th = PackageManager.FindAssetByName(sd.mapThemeRef, UserAssetType.MapThemeMetaData);
                    if (th != null) { meta.m_MapThemeMetaData = th.Instantiate<MapThemeMetaData>(); meta.m_MapThemeMetaData.SetSelfRef(th); }
                }
                Singleton<LoadingManager>.instance.LoadLevel(sd.assetRef ?? asset, "Game", "InGame", meta);
            }
        }

        // ------------------------------------------------------------ snapshot
        static Dictionary<string, object> D(params object[] kv) { var d = new Dictionary<string, object>(); for (int i = 0; i + 1 < kv.Length; i += 2) d[(string)kv[i]] = kv[i + 1]; return d; }
        static string Cut(string s) { if (s == null) return null; s = s.Replace("\n", " ").Trim(); return s.Length > 200 ? s.Substring(0, 200) : s; }

        static string Status()
        {
            var lm = Singleton<LoadingManager>.instance;
            if (lm.m_currentlyLoading) return "loading";
            if (lm.m_loadingComplete && Singleton<ToolManager>.exists && Singleton<ToolManager>.instance.m_properties != null
                && Singleton<ToolManager>.instance.m_properties.m_mode == ItemClass.Availability.Game) return "in_game";
            if (FindObjectOfType<MainMenu>() != null) return "menu";
            return lm.m_loadingComplete ? "other" : "starting";
        }

        static Dictionary<string, object> Snapshot(string id)
        {
            var st = Status();
            var engine = D("status", st, "city", null, "map", null, "date", null, "paused", null, "speed", null, "money", null, "population", null,
                           "happiness", null, "demand", null, "counts", null, "camera", null, "tool", null, "mods", Mods(), "time_of_day", null, "transport", null);
            var mod = new Dictionary<string, object>(); var text = new List<string>();
            var vars = Vars(); if (vars.Count > 0) mod["vars"] = vars;
            if (st == "in_game")
            {
                var sm = Singleton<SimulationManager>.instance; var d0 = Singleton<DistrictManager>.instance.m_districts.m_buffer[0];
                var zm = Singleton<ZoneManager>.instance;
                engine["city"] = sm.m_metaData != null ? sm.m_metaData.m_CityName : null; engine["map"] = sm.m_metaData != null ? sm.m_metaData.m_MapName : null;
                engine["date"] = sm.m_currentGameTime.ToString("yyyy-MM-dd HH:mm"); engine["paused"] = sm.SimulationPaused; engine["speed"] = sm.SelectedSimulationSpeed;
                engine["money"] = Singleton<EconomyManager>.instance.LastCashAmount / 100.0; engine["population"] = (long)d0.m_populationData.m_finalCount;
                engine["happiness"] = (int)d0.m_finalHappiness;
                engine["demand"] = D("residential", zm.m_residentialDemand, "commercial", zm.m_commercialDemand, "workplace", zm.m_workplaceDemand);
                engine["counts"] = D("buildings", (int)Singleton<BuildingManager>.instance.m_buildingCount, "segments", (int)Singleton<NetManager>.instance.m_segmentCount,
                                     "vehicles", (int)Singleton<VehicleManager>.instance.m_vehicleCount, "citizens", (int)Singleton<CitizenManager>.instance.m_citizenCount);
                var c = ToolsModifierControl.cameraController;
                if (c != null) engine["camera"] = D("x", Math.Round(c.m_targetPosition.x, 1), "z", Math.Round(c.m_targetPosition.z, 1), "height", Math.Round(c.m_targetPosition.y, 1),
                                                    "size", Math.Round(c.m_targetSize, 1), "angle", Math.Round(c.m_targetAngle.x, 1));
                var tc = ToolsModifierControl.toolController;
                engine["tool"] = tc != null && tc.CurrentTool != null ? tc.CurrentTool.GetType().Name : null;
                try { engine["time_of_day"] = TimeOfDay(); } catch (Exception e) { engine["time_of_day"] = D("error", e.GetType().Name); }
                try { engine["transport"] = Transport(); } catch (Exception e) { engine["transport"] = D("error", e.GetType().Name); }
                Custom(mod);
                var districts = new Dictionary<string, object>();
                var dm = Singleton<DistrictManager>.instance;
                for (int i = 1; i < dm.m_districts.m_size && districts.Count < 20; i++)
                    if ((dm.m_districts.m_buffer[i].m_flags & District.Flags.Created) != 0)
                        districts[Cut(dm.GetDistrictName(i)) ?? ("#" + i)] = D("population", (long)dm.m_districts.m_buffer[i].m_populationData.m_finalCount);
                if (districts.Count > 0) mod["districts"] = districts;
                try
                {
                    var mm = Singleton<MessageManager>.instance; var recent = mm.GetRecentMessages();
                    if (recent != null) { int k = 0; for (int i = recent.Length - 1; i >= 0 && k < 6; i--) if (recent[i] != null) { text.Add("chirp: " + Cut(recent[i].GetSenderName() + ": " + recent[i].GetText())); k++; } }
                }
                catch { }
            }
            UITexts(text);
            return D("id", id, "running", true, "frame", frame, "engine", engine, "mod", mod, "screen_text", text);
        }

        const BindingFlags ANY = BindingFlags.Instance | BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic;
        static object Fld(object o, Type t, string name)
        {
            var f = t.GetField(name, ANY); if (f != null) return f.GetValue(o);
            var p = t.GetProperty(name, ANY); return p != null ? p.GetValue(o, null) : null;
        }
        static object Call(object o, Type t, string name, params object[] args)
        {
            foreach (var m in t.GetMethods(ANY)) if (m.Name == name && m.GetParameters().Length == args.Length) return m.Invoke(o, args);
            return null;
        }

        static Dictionary<string, object> TimeOfDay()
        {
            var sm = Singleton<SimulationManager>.instance; var T = typeof(SimulationManager);
            var h = Fld(sm, T, "m_currentDayTimeHour");
            return D("hour", h == null ? null : (object)Math.Round(Convert.ToDouble(h), 2), "night", Fld(sm, T, "m_isNightTime"), "day_night_cycle", Fld(sm, T, "m_enableDayNight"));
        }

        static Dictionary<string, object> Transport()
        {
            var tm = Singleton<TransportManager>.instance; var byType = new Dictionary<string, object>(); var list = new List<object>();
            int lines = 0, vehicles = 0;
            for (ushort i = 1; i < tm.m_lines.m_size; i++)
            {
                object line = tm.m_lines.m_buffer[i];                                // a boxed copy: read only
                var flags = Convert.ToString(Fld(line, typeof(TransportLine), "m_flags"));
                if (flags == null || !flags.Contains("Created") || flags.Contains("Temporary")) continue;
                lines++;
                var info = Fld(line, typeof(TransportLine), "Info");
                var type = info != null ? Convert.ToString(Fld(info, info.GetType(), "m_transportType")) : "?";
                byType[type] = (byType.ContainsKey(type) ? (int)byType[type] : 0) + 1;
                var nv = Call(line, typeof(TransportLine), "CountVehicles", i); if (nv is int) vehicles += (int)nv;
                if (list.Count < 10)
                    list.Add(D("name", Cut(Convert.ToString(Call(tm, typeof(TransportManager), "GetLineName", i))), "type", type,
                               "stops", Call(line, typeof(TransportLine), "CountStops", i), "vehicles", nv, "complete", flags.Contains("Complete")));
            }
            return D("lines", lines, "by_type", byType, "vehicles", vehicles, "list", list);
        }

        static List<object> Mods()
        {
            var l = new List<object>();
            foreach (var pi in Singleton<PluginManager>.instance.GetPluginsInfo())
                if (!pi.isBuiltin && pi.isEnabled && !Harness.Contains(pi.name)) l.Add(pi.name);
            return l;
        }

        static IEnumerable<Assembly> ModAssemblies()
        {
            foreach (var pi in Singleton<PluginManager>.instance.GetPluginsInfo())
            {
                if (pi.isBuiltin || !pi.isEnabled || Harness.Contains(pi.name)) continue;
                List<Assembly> asms; try { asms = pi.GetAssemblies(); } catch { continue; }
                foreach (var a in asms) yield return a;
            }
        }

        // stock prefab names (written by a vanilla run on request "<id>:dump")
        static void DumpStock()
        {
            var names = new List<string>();
            for (uint i = 0; i < PrefabCollection<BuildingInfo>.LoadedCount(); i++) { var p = PrefabCollection<BuildingInfo>.GetLoaded(i); if (p != null) names.Add("b:" + p.name); }
            for (uint i = 0; i < PrefabCollection<NetInfo>.LoadedCount(); i++) { var p = PrefabCollection<NetInfo>.GetLoaded(i); if (p != null) names.Add("n:" + p.name); }
            for (uint i = 0; i < PrefabCollection<PropInfo>.LoadedCount(); i++) { var p = PrefabCollection<PropInfo>.GetLoaded(i); if (p != null) names.Add("p:" + p.name); }
            File.WriteAllText(Path.Combine(dir, "../stock_prefabs.txt"), string.Join("\n", names.ToArray()));
            stock = null;
        }

        static bool IsStock(string kind, PrefabInfo p)
        {
            if (p == null) return true;
            if (p.m_isCustomContent) return false;
            if (stock == null)
            {
                stock = new HashSet<string>();
                var f = Path.Combine(dir, "../stock_prefabs.txt");
                if (File.Exists(f)) foreach (var l in File.ReadAllLines(f)) stock.Add(l);
            }
            return stock.Count == 0 || stock.Contains(kind + ":" + p.name);
        }

        static void Custom(Dictionary<string, object> mod)
        {
            var b = new Dictionary<string, object>(); var bm = Singleton<BuildingManager>.instance;
            for (int i = 1; i < bm.m_buildings.m_size; i++)
            {
                var x = bm.m_buildings.m_buffer[i];
                if ((x.m_flags & Building.Flags.Created) == 0 || IsStock("b", x.Info)) continue;
                string k = x.Info.name; Dictionary<string, object> e;
                if (b.ContainsKey(k)) { e = (Dictionary<string, object>)b[k]; e["count"] = (int)e["count"] + 1; }
                else if (b.Count < 20) b[k] = D("count", 1, "pos", new double[] { Math.Round(x.m_position.x, 1), Math.Round(x.m_position.z, 1) });
            }
            if (b.Count > 0) mod["buildings"] = b;
            var n = new Dictionary<string, object>(); var nm = Singleton<NetManager>.instance;
            for (int i = 1; i < nm.m_segments.m_size; i++)
            {
                var s = nm.m_segments.m_buffer[i];
                if ((s.m_flags & NetSegment.Flags.Created) == 0 || IsStock("n", s.Info)) continue;
                string k = s.Info.name; n[k] = (n.ContainsKey(k) ? (int)n[k] : 0) + 1;
                if (n.Count >= 20) break;
            }
            if (n.Count > 0) mod["networks"] = n;
            var pr = new Dictionary<string, object>(); var pm = Singleton<PropManager>.instance;
            for (int i = 1; i < pm.m_props.m_size; i++)
            {
                var p = pm.m_props.m_buffer[i];
                if ((p.m_flags & (ushort)PropInstance.Flags.Created) == 0 || IsStock("p", p.Info)) continue;
                string k = p.Info.name; pr[k] = (pr.ContainsKey(k) ? (int)pr[k] : 0) + 1;
                if (pr.Count >= 20) break;
            }
            if (pr.Count > 0) mod["props"] = pr;
        }

        static Dictionary<string, object> Vars()
        {
            var v = new Dictionary<string, object>();
            foreach (var a in ModAssemblies())
            {
                Type[] ts; try { ts = a.GetTypes(); } catch (ReflectionTypeLoadException e) { ts = e.Types; }
                foreach (var t in ts)
                {
                    if (t == null || t.IsGenericTypeDefinition || t.Name.Contains("<")) continue;
                    FieldInfo[] fs; try { fs = t.GetFields(BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.DeclaredOnly); } catch { continue; }
                    foreach (var f in fs)
                    {
                        if (f.IsLiteral || f.IsInitOnly || f.Name.Contains("<")) continue;
                        object val; try { val = f.GetValue(null); } catch { continue; }
                        if (val == null || val is bool || val is int || val is long || val is float || val is double || val is string || val is Enum)
                        {
                            v[a.GetName().Name + "." + t.Name + "." + f.Name] = val is string ? (object)Cut((string)val) : val is Enum ? val.ToString() : val is float ? Math.Round((float)val, 3) : val;
                            if (v.Count >= 40) return v;
                        }
                    }
                }
            }
            return v;
        }

        static void UITexts(List<string> text)
        {
            var asms = new HashSet<Assembly>(ModAssemblies());
            if (asms.Count == 0) return;
            foreach (var l in FindObjectsOfType<UILabel>())
            {
                if (text.Count >= 30) break;
                if (l == null || !l.isVisible || string.IsNullOrEmpty(l.text)) continue;
                bool ours = false;
                for (UIComponent c = l; c != null && !ours; c = c.parent) ours = asms.Contains(c.GetType().Assembly);
                if (ours) text.Add("ui: " + Cut(l.text));
            }
        }

        // ------------------------------------------------------------ JSON (no JSON library in the game's .NET 3.5)
        static string J(object o)
        {
            var sb = new StringBuilder(); W(sb, o); return sb.ToString();
        }

        static void W(StringBuilder sb, object o)
        {
            if (o == null) { sb.Append("null"); return; }
            if (o is string) { S(sb, (string)o); return; }
            if (o is bool) { sb.Append((bool)o ? "true" : "false"); return; }
            if (o is int || o is long || o is uint || o is ushort || o is byte || o is short) { sb.Append(Convert.ToString(o, System.Globalization.CultureInfo.InvariantCulture)); return; }
            if (o is float || o is double)
            {
                double d = Convert.ToDouble(o);
                sb.Append(double.IsNaN(d) || double.IsInfinity(d) ? "null" : d.ToString("R", System.Globalization.CultureInfo.InvariantCulture)); return;
            }
            var dict = o as IDictionary;
            if (dict != null)
            {
                sb.Append('{'); bool first = true;
                foreach (DictionaryEntry e in dict) { if (!first) sb.Append(','); first = false; S(sb, Convert.ToString(e.Key)); sb.Append(':'); W(sb, e.Value); }
                sb.Append('}'); return;
            }
            var en = o as IEnumerable;
            if (en != null) { sb.Append('['); bool first = true; foreach (var x in en) { if (!first) sb.Append(','); first = false; W(sb, x); } sb.Append(']'); return; }
            S(sb, o.ToString());
        }

        static void S(StringBuilder sb, string s)
        {
            sb.Append('"');
            foreach (var ch in s)
            {
                switch (ch)
                {
                    case '"': sb.Append("\\\""); break;
                    case '\\': sb.Append("\\\\"); break;
                    case '\n': sb.Append("\\n"); break;
                    case '\r': sb.Append("\\r"); break;
                    case '\t': sb.Append("\\t"); break;
                    default: if (ch < 0x20) sb.Append("\\u" + ((int)ch).ToString("x4")); else sb.Append(ch); break;
                }
            }
            sb.Append('"');
        }
    }
}
