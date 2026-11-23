// GameAnything-Bench regression probe bridge for Cities: Skylines 1 (1.21.x, Linux). A normal ICities mod.
// Inactive unless the file probe.cfg sits next to the DLL (start-game.sh runs the game with a minimal environment, so the
// switches come from that file): line 1 = mode (run1 | run2), line 2 = comma list of groups, line 3 = output .jsonl path.
// run1: on the main menu, a new city on the stock map "Green Plains"; then the probes (content, sim, camera, infoviews,
//       daynight, roads, zoning, services, growth = traffic/population/economy over ~6 in-game weeks, perf, transport = a bus line
//       in the grown city), then an autosave "gaprobe".
// 2026-11-05 (gab, written without the game: UNTESTED, not even compiled -- the first `gab selfcheck cities_skylines` builds and
//       runs it): groups daynight and transport. Members whose exact name / signature could not be checked against the game's
//       assemblies are reached by reflection (Fld / SetFld / Call below), so a wrong guess shows up as a recorded error / null
//       field in the session, never as a compile error of the whole probe mod.
// 2026-11-05 later (same status: UNTESTED, not compiled): probe.cfg lines 4+ are optional key=value switches: start=<new:Map |
//       load:Save> (default new:Green Plains), fixture=<fixture.cfg> (the probe-city fixture: P0 / Dir of its road layout, so the
//       groups that run on a loaded fixture city find its main road), turbo=0|1 (default 1). Group "fixture": saves the grown city as
//       "gafixture" + writes fixture.cfg next to the output (gab caches both per game version; transport then runs on the loaded
//       fixture instead of regrowing a city). FastSim(): speed 3, then tries to run the simulation faster than speed 3 (setting
//       SelectedSimulationSpeed / a speed field above 3 by reflection) and keeps it only when the simulation frame rate really rose
//       by >= 30% (recorded as _turbo; fields starting with _ are not compared).
// run2: loads the save "gaprobe" and reads back what run1 recorded.
// Building goes through the game's own code: NetTool.CreateNode (the road tool's routine, with money), ZoneBlock.SetZone +
// RefreshZoning (the zone tool's routine), BuildingManager.CreateBuilding; the simulation runs at speed 3.
using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using ColossalFramework;
using ColossalFramework.Math;
using ColossalFramework.Packaging;
using ColossalFramework.UI;
using ICities;
using UnityEngine;

namespace CSProbe
{
    public class Mod : IUserMod
    {
        public string Name { get { Host.Ensure(); return "GameAnything CSProbe"; } }
        public string Description { get { return "GameAnything-Bench regression probes (inactive without probe.cfg)"; } }
    }

    public class Loading : LoadingExtensionBase
    {
        public override void OnLevelLoaded(LoadMode mode) { Host.Ensure(); Host.Inst.LevelLoaded(); }
    }

    public static class J
    {
        public static string Esc(string s) { return s == null ? "null" : "\"" + s.Replace("\\", "\\\\").Replace("\"", "\\\"").Replace("\n", "\\n") + "\""; }
        public static string V(object o)
        {
            if (o == null) return "null";
            if (o is string) return Esc((string)o);
            if (o is bool) return (bool)o ? "true" : "false";
            if (o is float) return ((float)o).ToString("R", System.Globalization.CultureInfo.InvariantCulture);
            if (o is double) return ((double)o).ToString("R", System.Globalization.CultureInfo.InvariantCulture);
            if (o is int || o is long || o is uint || o is ushort || o is byte || o is short) return Convert.ToString(o, System.Globalization.CultureInfo.InvariantCulture);
            var d = o as IDictionary;
            if (d != null) { var sb = new StringBuilder("{"); bool f = true; foreach (DictionaryEntry e in d) { if (!f) sb.Append(","); f = false; sb.Append(Esc(e.Key.ToString())).Append(":").Append(V(e.Value)); } return sb.Append("}").ToString(); }
            var l = o as IEnumerable;
            if (l != null) { var sb = new StringBuilder("["); bool f = true; foreach (var x in l) { if (!f) sb.Append(","); f = false; sb.Append(V(x)); } return sb.Append("]").ToString(); }
            return Esc(o.ToString());
        }
        public static string H(string s) { using (var m = System.Security.Cryptography.MD5.Create()) return BitConverter.ToString(m.ComputeHash(Encoding.UTF8.GetBytes(s))).Replace("-", "").Substring(0, 12); }
        public static double R(double v, int n = 2) { return Math.Round(v, n); }
    }

    public class Host : MonoBehaviour
    {
        public static Host Inst;
        string mode, outPath, startArg = "new:Green Plains", fixtureCfg; bool turboOn = true; HashSet<string> groups = new HashSet<string>(); bool active, started, levelLoaded; float menuSince = -1;

        public static void Ensure()
        {
            if (Inst != null) return;
            var go = new GameObject("GAProbeHost"); DontDestroyOnLoad(go); Inst = go.AddComponent<Host>();
        }

        void Awake()
        {
            try
            {
                string dir = null;
                foreach (var p in Singleton<ColossalFramework.Plugins.PluginManager>.instance.GetPluginsInfo())
                    if (p.userModInstance is Mod) dir = p.modPath;
                var cfg = dir != null ? Path.Combine(dir, "probe.cfg") : null;
                if (cfg != null && File.Exists(cfg))
                {
                    var l = File.ReadAllLines(cfg);
                    mode = l[0].Trim(); foreach (var g in l[1].Split(',')) groups.Add(g.Trim()); outPath = l[2].Trim(); active = true;
                    for (int k = 3; k < l.Length; k++)
                    {
                        var eq = l[k].IndexOf('='); if (eq < 0) continue;
                        string key = l[k].Substring(0, eq).Trim(), val = l[k].Substring(eq + 1).Trim();
                        if (key == "start") startArg = val; else if (key == "fixture") fixtureCfg = val; else if (key == "turbo") turboOn = val != "0";
                    }
                    foreach (var p in Singleton<ColossalFramework.Plugins.PluginManager>.instance.GetPluginsInfo())   // the mods under test
                        if (!p.isBuiltin && p.userModInstance != null && !p.isEnabled) p.isEnabled = true;
                    Emit("_boot", new Hashtable { { "mode", mode }, { "mods", ModList() } });
                }
            }
            catch (Exception e) { Debug.Log("[CSProbe] cfg: " + e); }
        }

        static ArrayList ModList()
        {
            var l = new ArrayList();
            foreach (var p in Singleton<ColossalFramework.Plugins.PluginManager>.instance.GetPluginsInfo())
                if (!p.isBuiltin) l.Add(p.name + (p.isEnabled ? "" : " (off)"));
            return l;
        }

        public void Emit(string probe, Hashtable d)
        {
            d["probe"] = probe;
            var line = "CSPROBE " + J.V(d);
            try { File.AppendAllText(outPath, line + "\n"); } catch { }
            Debug.Log("[CSProbe] " + (line.Length > 300 ? line.Substring(0, 300) : line));
        }

        static string Status()
        {
            var lm = Singleton<LoadingManager>.instance;
            if (lm.m_currentlyLoading) return "loading";
            if (lm.m_loadingComplete && Singleton<ToolManager>.exists && Singleton<ToolManager>.instance.m_properties != null
                && Singleton<ToolManager>.instance.m_properties.m_mode == ItemClass.Availability.Game) return "in_game";
            if (FindObjectOfType<MainMenu>() != null) return "menu";
            return "other";
        }

        void Update()
        {
            if (!active || started) return;
            if (Status() == "menu") { if (menuSince < 0) menuSince = Time.realtimeSinceStartup; } else menuSince = -1;
            if (menuSince >= 0 && Time.realtimeSinceStartup - menuSince > 4f)
            {
                started = true;
                // never past a legal / terms dialog: those are the user's to accept (the runner screenshots and reports it)
                foreach (var n in new[] { "LegalDisclaimerPanel", "DisclaimerPanel" })
                {
                    UIComponent c = null; try { c = UIView.library.Get(n); } catch { }
                    if (c != null && c.isVisible)
                    {
                        var docs = new ArrayList();
                        try { foreach (var dd in Singleton<PopsManager>.instance.UnreadLegalDocuments) docs.Add(new Hashtable { { "type", dd.Type }, { "title", dd.Title }, { "url", dd.Url }, { "key", "legal." + dd.Type + "." + dd.Title } }); } catch (Exception e) { docs.Add(e.Message); }
                        Emit("_fatal", new Hashtable { { "error", "DIALOG " + n + " is showing: not clicked, not started" }, { "unread_legal_documents", docs } }); return;
                    }
                }
                try { Emit("_start", new Hashtable { { "start", Start_(mode == "run2" ? "load:gaprobe" : startArg) } }); }
                catch (Exception e) { Emit("_fatal", new Hashtable { { "error", "start: " + e } }); }
            }
        }

        public void LevelLoaded()
        {
            if (!active || levelLoaded) return;
            levelLoaded = true; StartCoroutine(Run());
        }

        // ---------------------------------------------------------------- start a city (gamebench/bridges/cities Commands.Reset)
        static string Start_(string start)
        {
            var colon = start.IndexOf(':'); var kind = start.Substring(0, colon); var arg = start.Substring(colon + 1);
            foreach (var n in new[] { "NewGamePanel", "LoadPanel", "ContentManagerPanel", "OptionsPanel" }) try { UIView.library.Hide(n); } catch { }
            if (kind == "new")
            {
                foreach (var a in PackageManager.FilterAssets(UserAssetType.MapMetaData))
                {
                    if (a == null || !a.isEnabled) continue;
                    var md = a.Instantiate<MapMetaData>(); if (md == null || (md.mapName != arg && a.name != arg)) continue;
                    var meta = new SimulationMetaData
                    {
                        m_CityName = "GAProbe", m_gameInstanceIdentifier = Guid.NewGuid().ToString(), m_invertTraffic = SimulationMetaData.MetaBool.False,
                        m_disableAchievements = SimulationMetaData.MetaBool.True, m_startingDateTime = new DateTime(2026, 1, 1), m_currentDateTime = new DateTime(2026, 1, 1),
                        m_newGameAppVersion = 222554386u, m_updateMode = SimulationManager.UpdateMode.NewGameFromMap, m_MapName = md.mapName
                    };
                    if (md.mapThemeRef != null) { var th = PackageManager.FindAssetByName(md.mapThemeRef, UserAssetType.MapThemeMetaData); if (th != null) { meta.m_MapThemeMetaData = th.Instantiate<MapThemeMetaData>(); meta.m_MapThemeMetaData.SetSelfRef(th); } }
                    Singleton<LoadingManager>.instance.LoadLevel(md.assetRef ?? a, "Game", "InGame", meta);
                    return "new " + md.mapName;
                }
                throw new Exception("no map " + arg);
            }
            foreach (var a in PackageManager.FilterAssets(UserAssetType.SaveGameMetaData))
            {
                if (a == null || !a.isEnabled || a.name != arg) continue;
                var sd = a.Instantiate<SaveGameMetaData>();
                var meta = new SimulationMetaData { m_CityName = sd.cityName, m_updateMode = SimulationManager.UpdateMode.LoadGame };
                if (sd.mapThemeRef != null) { var th = PackageManager.FindAssetByName(sd.mapThemeRef, UserAssetType.MapThemeMetaData); if (th != null) { meta.m_MapThemeMetaData = th.Instantiate<MapThemeMetaData>(); meta.m_MapThemeMetaData.SetSelfRef(th); } }
                Singleton<LoadingManager>.instance.LoadLevel(sd.assetRef ?? a, "Game", "InGame", meta);
                return "load " + arg;
            }
            throw new Exception("no save " + arg);
        }

        // ---------------------------------------------------------------- helpers
        static SimulationManager Sim { get { return Singleton<SimulationManager>.instance; } }
        static District D0 { get { return Singleton<DistrictManager>.instance.m_districts.m_buffer[0]; } }
        static long Cash { get { return Singleton<EconomyManager>.instance.LastCashAmount; } }
        static float H(Vector3 p) { return Singleton<TerrainManager>.instance.SampleRawHeightSmooth(p); }
        static IEnumerator Frames(int n) { for (int i = 0; i < n; i++) yield return null; }

        object simResult; bool simDone;
        IEnumerator OnSim(Func<object> f)
        {
            simDone = false; simResult = null;
            Sim.AddAction(() => { try { simResult = f(); } catch (Exception e) { simResult = "ERR " + e.Message; } simDone = true; });
            float t = Time.realtimeSinceStartup;
            while (!simDone && Time.realtimeSinceStartup - t < 30) yield return null;
        }

        static ushort roadNodeA, lastSeg; static Vector3 P0, Dir, Side;
        static string Road(string info, Vector3 a, Vector3 b, ushort startNode, ushort endNode, out ushort firstNode, out ushort lastNode)
        {
            var ni = PrefabCollection<NetInfo>.FindLoaded(info);
            firstNode = lastNode = 0;
            if (ni == null) return "no NetInfo " + info;
            a.y = H(a); b.y = H(b);
            var d = (b - a); d.y = 0; d.Normalize();
            var s = new NetTool.ControlPoint { m_position = a, m_direction = d, m_node = startNode };
            var e = new NetTool.ControlPoint { m_position = b, m_direction = d, m_node = endNode };
            var m = new NetTool.ControlPoint { m_position = (a + b) * 0.5f, m_direction = d };
            ushort seg; int cost, prod;
            var err = NetTool.CreateNode(ni, s, m, e, NetTool.m_nodePositionsSimulation, 1000, false, false, false, true, true, false, false, 0, out firstNode, out lastNode, out seg, out cost, out prod);
            lastSeg = seg;
            return err == ToolBase.ToolErrors.None ? "ok" : err.ToString();
        }

        IEnumerator Run()
        {
            yield return Frames(120);
            Sim.SimulationPaused = true;
            Emit("_entered", new Hashtable { { "city", Sim.m_metaData.m_CityName }, { "map", Sim.m_metaData.m_MapName }, { "date", Sim.m_currentGameTime.ToString("yyyy-MM-dd") }, { "mods", ModList() } });
            if (mode == "run2") { yield return StartCoroutine(SaveLoad()); Emit("_done", new Hashtable()); yield return Frames(30); Application.Quit(); yield break; }
            if (!string.IsNullOrEmpty(fixtureCfg))       // a loaded fixture city: its road layout from the run that grew it
            {
                try
                {
                    var v = File.ReadAllText(fixtureCfg).Trim().Split(' ').Select(s => float.Parse(s, System.Globalization.CultureInfo.InvariantCulture)).ToArray();
                    P0 = new Vector3(v[0], v[1], v[2]); Dir = new Vector3(v[3], 0, v[4]); Side = new Vector3(-Dir.z, 0, Dir.x);
                    Emit("_fixture", new Hashtable { { "population", (int)D0.m_populationData.m_finalCount }, { "segments", (int)Singleton<NetManager>.instance.m_segmentCount } });
                }
                catch (Exception e) { Emit("_fatal", new Hashtable { { "error", "fixture.cfg: " + e.Message } }); yield break; }
            }
            var order = new[] { "content", "sim", "camera", "infoviews", "daynight", "roads", "zoning", "services", "growth", "fixture", "transport", "save" };
            foreach (var g in order)
            {
                if (!groups.Contains(g)) continue;
                var d = new Hashtable(); cur = d; IEnumerator e = null;
                switch (g)
                {
                    case "content": e = Content(); break; case "sim": e = SimP(); break; case "camera": e = Camera(); break; case "infoviews": e = Info(); break;
                    case "roads": e = Roads(); break; case "zoning": e = Zoning(); break; case "services": e = Services(); break; case "growth": e = Growth(); break;
                    case "save": e = Save(); break; case "daynight": e = DayNight(); break; case "transport": e = Transport(); break; case "fixture": e = Fixture(); break;
                }
                while (true)
                {
                    object c; bool more;
                    try { more = e.MoveNext(); c = more ? e.Current : null; }
                    catch (Exception ex) { d["error"] = ex.GetType().Name + ": " + ex.Message + " @ " + (ex.StackTrace ?? "").Split('\n')[0].Trim(); break; }
                    if (!more) break;
                    yield return c;
                }
                Emit(g, d);
            }
            Emit("_done", new Hashtable());
            yield return Frames(30);
            Application.Quit();
        }
        Hashtable cur;

        // ---- content: stock prefabs (buildings, networks, vehicles) with their key stats
        IEnumerator Content()
        {
            var b = new Hashtable(); var n = new Hashtable(); var v = new Hashtable();
            for (uint i = 0; i < PrefabCollection<BuildingInfo>.LoadedCount(); i++)
            {
                var x = PrefabCollection<BuildingInfo>.GetLoaded(i); if (x == null) continue;
                int cc = 0, mc = 0; try { cc = x.m_buildingAI.GetConstructionCost(); mc = x.m_buildingAI.GetMaintenanceCost(); } catch { }
                b[x.name] = J.H(string.Join("|", new[] { x.m_class != null ? x.m_class.m_service + "/" + x.m_class.m_subService + "/" + x.m_class.m_level : "", x.m_cellWidth + "x" + x.m_cellLength,
                    cc.ToString(), mc.ToString(), x.m_buildingAI != null ? x.m_buildingAI.GetType().Name : "", x.m_placementMode.ToString() }));
            }
            for (uint i = 0; i < PrefabCollection<NetInfo>.LoadedCount(); i++)
            {
                var x = PrefabCollection<NetInfo>.GetLoaded(i); if (x == null) continue;
                var lanes = x.m_lanes == null ? "" : string.Join(";", x.m_lanes.Select(l => l.m_laneType + ":" + l.m_vehicleType + ":" + l.m_speedLimit.ToString("F2") + ":" + l.m_position.ToString("F1")).ToArray());
                n[x.name] = J.H(x.m_halfWidth.ToString("F2") + "|" + (x.m_class != null ? x.m_class.m_service + "/" + x.m_class.m_level : "") + "|" + lanes + "|" + (x.m_netAI != null ? x.m_netAI.GetType().Name + x.m_netAI.GetConstructionCost(Vector3.zero, new Vector3(100, 0, 0), 0, 0) : ""));
            }
            for (uint i = 0; i < PrefabCollection<VehicleInfo>.LoadedCount(); i++)
            {
                var x = PrefabCollection<VehicleInfo>.GetLoaded(i); if (x == null) continue;
                v[x.name] = J.H(x.m_maxSpeed.ToString("F2") + "|" + x.m_acceleration.ToString("F2") + "|" + x.m_braking.ToString("F2") + "|" + x.m_vehicleType + "|" + (x.m_vehicleAI != null ? x.m_vehicleAI.GetType().Name : ""));
            }
            cur["buildings"] = b; cur["nets"] = n; cur["vehicles"] = v;
            cur["n_buildings"] = b.Count; cur["n_nets"] = n.Count; cur["n_vehicles"] = v.Count; cur["n_citizens"] = (int)PrefabCollection<CitizenInfo>.LoadedCount();
            var et = Singleton<EconomyManager>.instance;
            cur["tax_res_low"] = et.GetTaxRate(ItemClass.Service.Residential, ItemClass.SubService.ResidentialLow, ItemClass.Level.Level1);
            cur["tax_com_low"] = et.GetTaxRate(ItemClass.Service.Commercial, ItemClass.SubService.CommercialLow, ItemClass.Level.Level1);
            cur["tax_ind"] = et.GetTaxRate(ItemClass.Service.Industrial, ItemClass.SubService.IndustrialGeneric, ItemClass.Level.Level1);
            cur["start_cash"] = Cash / 100;
            yield break;
        }

        // ---- sim: simulation frames per real second at speeds 1-3; pause stops the clock
        IEnumerator SimP()
        {
            var r = new Hashtable();
            foreach (int s in new[] { 1, 2, 3 })
            {
                Sim.SimulationPaused = false; Sim.SelectedSimulationSpeed = s; yield return new WaitForSeconds(1f);
                uint f0 = Sim.m_currentFrameIndex; var t0 = Sim.m_currentGameTime; float w0 = Time.realtimeSinceStartup;
                yield return new WaitForSeconds(5f);
                float dt = Time.realtimeSinceStartup - w0;
                r["speed" + s + "_fps"] = J.R((Sim.m_currentFrameIndex - f0) / dt, 1);
                r["speed" + s + "_game_hours_per_s"] = J.R((Sim.m_currentGameTime - t0).TotalHours / dt, 2);
                r["speed" + s + "_game_s_per_frame"] = J.R((Sim.m_currentGameTime - t0).TotalSeconds / Math.Max(1, Sim.m_currentFrameIndex - f0), 2);
            }
            Sim.SimulationPaused = true; yield return new WaitForSeconds(1f);
            uint p0 = Sim.m_currentFrameIndex; yield return new WaitForSeconds(3f);
            r["paused_frames_3s"] = (int)(Sim.m_currentFrameIndex - p0);
            foreach (DictionaryEntry e in r) cur[e.Key] = e.Value;
            // ratio speed3 / speed1 (machine independent), game seconds per frame (constant)
            cur["ratio_3_to_1"] = J.R((double)r["speed3_fps"] / Math.Max(0.1, (double)r["speed1_fps"]), 1);
            cur["ratio_2_to_1"] = J.R((double)r["speed2_fps"] / Math.Max(0.1, (double)r["speed1_fps"]), 1);
        }

        // ---- camera: the camera controller moves, zooms and rotates to set targets; free camera hides the UI
        IEnumerator Camera()
        {
            var c = ToolsModifierControl.cameraController;
            var p = new Vector3(200, 0, -300); p.y = H(p);
            c.m_targetPosition = p; c.m_targetSize = 400; c.m_targetAngle = new Vector2(45, 35);
            yield return new WaitForSeconds(4f);
            cur["pos_err"] = J.R(Vector3.Distance(new Vector3(c.m_currentPosition.x, 0, c.m_currentPosition.z), new Vector3(p.x, 0, p.z)), 1);
            cur["size_err"] = J.R(Math.Abs(c.m_currentSize - 400), 1); cur["angle_err"] = J.R((c.m_currentAngle - new Vector2(45, 35)).magnitude, 1);
            cur["cam_height_above_ground"] = J.R(c.transform.position.y - H(c.transform.position), 0);
            c.m_freeCamera = true; yield return new WaitForSeconds(1.5f);
            cur["free_camera_hides_ui"] = !UIView.GetAView().enabled || !UIView.GetAView().gameObject.activeInHierarchy || UIView.GetAView().uiCamera == null || !UIView.GetAView().uiCamera.enabled;
            c.m_freeCamera = false; yield return new WaitForSeconds(1.5f);
            cur["ui_back"] = UIView.GetAView().uiCamera != null && UIView.GetAView().uiCamera.enabled;
        }

        // ---- info views: each opens (InfoManager current mode follows)
        IEnumerator Info()
        {
            var im = Singleton<InfoManager>.instance; var ok = new ArrayList(); var bad = new ArrayList();
            foreach (var name in new[] { "Electricity", "Water", "Garbage", "Traffic", "Density", "Happiness", "Pollution", "NoisePollution", "Health", "Education",
                                          "Wind", "Transport", "LandValue", "CrimeRate", "Connections", "BuildingLevel", "Heating", "Maintenance", "Fire", "Entertainment" })
            {
                InfoManager.InfoMode m;
                try { m = (InfoManager.InfoMode)Enum.Parse(typeof(InfoManager.InfoMode), name); } catch { bad.Add(name + "?"); continue; }
                im.SetCurrentMode(m, InfoManager.SubInfoMode.Default);
                yield return new WaitForSeconds(0.4f);
                if (im.CurrentMode == m) ok.Add(name); else bad.Add(name);
            }
            im.SetCurrentMode(InfoManager.InfoMode.None, InfoManager.SubInfoMode.Default);
            cur["opened"] = ok; cur["failed"] = bad;
        }

        // ---- roads: from the highway's dead end into the start tile, a main road with 4 junctions and 8 side roads (road tool code, with money)
        IEnumerator Roads()
        {
            var nm = Singleton<NetManager>.instance; ushort best = 0; float bd = 1e9f;
            for (ushort i = 1; i < nm.m_nodes.m_size; i++)
            {
                var nd = nm.m_nodes.m_buffer[i];
                if ((nd.m_flags & NetNode.Flags.Created) == 0 || nd.Info == null || nd.CountSegments() != 1 || !nd.Info.name.Contains("Highway")) continue;
                float dist = new Vector2(nd.m_position.x, nd.m_position.z).magnitude;
                if (dist < bd) { bd = dist; best = i; }
            }
            cur["highway_end"] = best != 0;
            if (best == 0) yield break;
            var node = nm.m_nodes.m_buffer[best]; ushort sg = 0;
            for (int k = 0; k < 8; k++) { sg = node.GetSegment(k); if (sg != 0) break; }
            var other = nm.m_segments.m_buffer[sg].GetOtherNode(best);
            P0 = node.m_position; Dir = P0 - nm.m_nodes.m_buffer[other].m_position; Dir.y = 0; Dir.Normalize(); Side = new Vector3(-Dir.z, 0, Dir.x);
            cur["highway_end_pos"] = new ArrayList { J.R(P0.x, 0), J.R(P0.z, 0) };
            int seg0 = nm.m_segmentCount, node0 = nm.m_nodeCount; long cash0 = Cash; var res = new ArrayList();
            var junction = new ushort[5]; junction[0] = best; Vector3 a = P0;
            for (int k = 1; k <= 4; k++)
            {
                var b = P0 + Dir * (40 + 96 * k); ushort f = 0, l = 0; ushort start = junction[k - 1];
                yield return StartCoroutine(OnSim(() => { ushort ff, ll; var r = Road("Basic Road", a, b, start, 0, out ff, out ll); f = ff; l = ll; return r; }));
                res.Add(simResult); junction[k] = l; a = b;
            }
            for (int k = 1; k <= 4; k++)
                foreach (int sgn in new[] { 1, -1 })
                {
                    var j = nm.m_nodes.m_buffer[junction[k]].m_position; var e = j + Side * sgn * 160; ushort jn = junction[k];
                    yield return StartCoroutine(OnSim(() => { ushort ff, ll; return Road("Basic Road", j, e, jn, 0, out ff, out ll); }));
                    res.Add(simResult);
                }
            yield return Frames(30);
            Sim.SimulationPaused = false; yield return new WaitForSeconds(1f); Sim.SimulationPaused = true;
            cur["results"] = res; cur["segments_added"] = nm.m_segmentCount - seg0; cur["nodes_added"] = nm.m_nodeCount - node0;
            cur["cost"] = (cash0 - Cash) / 100;
            int blocks = 0; var zm = Singleton<ZoneManager>.instance;
            for (int i = 1; i < zm.m_blocks.m_size; i++)
            {
                var zb = zm.m_blocks.m_buffer[i]; if (zb.m_flags == 0) continue;
                var q = zb.m_position - P0; if (Math.Abs(Vector3.Dot(q, Dir)) < 520 && Math.Abs(Vector3.Dot(q, Side)) < 220 && Vector3.Dot(q, Dir) > 0) blocks++;
            }
            cur["zone_blocks_created"] = blocks;
            // upgrade + bulldoze: an extra stub road is upgraded to Medium Road (the tool's upgrade = rebuild over the segment), then bulldozed
            var s1 = nm.m_nodes.m_buffer[junction[4]].m_position; var s2 = s1 + Dir * 96; ushort jn4 = junction[4]; ushort stubEnd = 0, stubSeg = 0;
            yield return StartCoroutine(OnSim(() => { ushort ff, ll; var r = Road("Basic Road", s1, s2, jn4, 0, out ff, out ll); stubEnd = ll; stubSeg = lastSeg; return r; }));
            cur["stub"] = simResult;
            yield return StartCoroutine(OnSim(() => { ushort ff, ll; return Road("Medium Road", s1, s2, jn4, stubEnd, out ff, out ll); }));
            cur["upgrade"] = simResult; yield return Frames(10);
            ushort seg2 = 0; var n4 = nm.m_nodes.m_buffer[stubEnd];
            for (int k = 0; k < 8; k++) if (n4.GetSegment(k) != 0) seg2 = n4.GetSegment(k);
            cur["upgraded_to"] = seg2 != 0 ? nm.m_segments.m_buffer[seg2].Info.name : null;
            int before = nm.m_segmentCount;
            yield return StartCoroutine(OnSim(() => { if (seg2 != 0) nm.ReleaseSegment(seg2, false); return "ok"; }));
            yield return Frames(10);
            cur["bulldozed"] = before - nm.m_segmentCount;
        }

        // ---- zoning: zone cells along the side roads (left side residential, right side industrial, main road commercial)
        IEnumerator Zoning()
        {
            var zm = Singleton<ZoneManager>.instance; int[] cells = new int[3];
            yield return StartCoroutine(OnSim(() =>
            {
                for (ushort i = 1; i < zm.m_blocks.m_size; i++)
                {
                    if (zm.m_blocks.m_buffer[i].m_flags == 0) continue;
                    var q = zm.m_blocks.m_buffer[i].m_position - P0; float along = Vector3.Dot(q, Dir), side = Vector3.Dot(q, Side);
                    if (along < 60 || along > 520 || Math.Abs(side) > 220) continue;
                    var z = Math.Abs(side) < 45 ? ItemClass.Zone.CommercialLow : side > 0 ? ItemClass.Zone.ResidentialLow : ItemClass.Zone.Industrial;
                    int idx = z == ItemClass.Zone.ResidentialLow ? 0 : z == ItemClass.Zone.CommercialLow ? 1 : 2;
                    bool ch = false; int rows = zm.m_blocks.m_buffer[i].RowCount;
                    for (int r = 0; r < rows; r++) for (int c = 0; c < 4; c++)
                        if (zm.m_blocks.m_buffer[i].GetZone(c, r) == ItemClass.Zone.Unzoned && zm.m_blocks.m_buffer[i].SetZone(c, r, z)) { ch = true; cells[idx]++; }
                    if (ch) zm.m_blocks.m_buffer[i].RefreshZoning(i);
                }
                return "ok";
            }));
            cur["res_cells"] = cells[0]; cur["com_cells"] = cells[1]; cur["ind_cells"] = cells[2];
            cur["demand_res"] = zm.m_residentialDemand; cur["demand_com"] = zm.m_commercialDemand; cur["demand_work"] = zm.m_workplaceDemand;
        }

        // ---- services: two wind turbines and a water tower beyond the residential (+Side) ends of the side roads, a landfill on a side road;
        //      a power line turbine -> turbine -> tower -> a spine past the side roads' ends with spurs between the residential strips, a
        //      water pipe from the tower's own pipe node into a main pipe along the main road with branches along the side roads.
        // 2026-11-15 (selfcheck 20261115-072225: population 0, every zoned building no_power + no_water, water_capacity 0): every piece is
        //      now joined at explicit nodes. A direct NetTool.CreateNode call does not make junctions where two nets cross (the net tool
        //      does that per frame), a water facility only pumps when its own pipe node (Building.m_netNode) has a segment, and a power
        //      line node conducts only into the electricity cell it stands in (a cell made conductive by a building nearby): the old pipe
        //      started beside the tower's node, crossed the main pipe without a junction, and the power line ended in the empty
        //      industrial zone and never reached the tower. Zoned buildings with a MajorProblem (no power / water for long) get no move-in
        //      offers (ResidentialBuildingAI), so nobody moved in.
        IEnumerator Services()
        {
            var bm = Singleton<BuildingManager>.instance; var placed = new ArrayList(); var ids = new ushort[4];
            Func<string, Vector3, float, int, string> place = (name, pos, an, slot) =>
            {
                var bi = PrefabCollection<BuildingInfo>.FindLoaded(name); if (bi == null) return name + ": no prefab";
                pos.y = H(pos); ushort id; var rnd = Sim.m_randomizer;
                bool ok = bm.CreateBuilding(out id, ref rnd, bi, pos, an, 0, Sim.m_currentBuildIndex);
                if (ok) { Sim.m_currentBuildIndex++; ids[slot] = id; Singleton<EconomyManager>.instance.FetchResource(EconomyManager.Resource.Construction, bi.m_buildingAI.GetConstructionCost(), bi.m_class); }
                return name + (ok ? ": ok" : ": failed");
            };
            float ang = Mathf.Atan2(Dir.x, -Dir.z);
            const float S = 185f;                                         // the spine: past the side roads' ends (160 + half width)
            var w1 = P0 + Dir * 300 + Side * 260; var w2 = P0 + Dir * 380 + Side * 260;
            var wt = P0 + Dir * 200 + Side * 260; var lf = P0 + Dir * 470 - Side * 120;
            yield return StartCoroutine(OnSim(() => place("Wind Turbine", w1, ang, 0))); placed.Add(simResult);
            yield return StartCoroutine(OnSim(() => place("Wind Turbine", w2, ang, 1))); placed.Add(simResult);
            yield return StartCoroutine(OnSim(() => place("Water Tower", wt, ang, 2))); placed.Add(simResult);
            yield return StartCoroutine(OnSim(() => place("Landfill Site", lf, ang, 3))); placed.Add(simResult);
            yield return Frames(5);
            var pl = new ArrayList(); var spineAt = new[] { 120f, 184f, 280f, 376f, 472f }; var spine = new ushort[spineAt.Length];
            // power: the spine (nodes at the spur positions, <= 48 m apart), the spurs into the gaps between the residential strips (side
            // road k at along 40 + 96k, its lots 8..40 m either side: the gap is at along 40 + 96k + 48), then turbines -> tower -> spine
            yield return StartCoroutine(OnSim(() =>
            {
                ushort prev = 0;
                for (int i = 0; i + 1 < spineAt.Length; i++)
                {
                    var r = Chain("Power Line", Pts(P0 + Dir * spineAt[i] + Side * S, P0 + Dir * spineAt[i + 1] + Side * S, 48), prev, 0, out ushort f, out ushort l);
                    if (r != "ok") return "spine: " + r;
                    if (i == 0) spine[0] = f; spine[i + 1] = l; prev = l;
                }
                return "ok";
            })); pl.Add(simResult);
            for (int i = 1; i < spineAt.Length; i++)
            {
                ushort sn = spine[i]; float al = spineAt[i];
                if (sn == 0) { pl.Add("spur " + al + ": no spine node"); continue; }
                yield return StartCoroutine(OnSim(() => Chain("Power Line", Pts(Singleton<NetManager>.instance.m_nodes.m_buffer[sn].m_position, P0 + Dir * al + Side * 50, 40), sn, 0, out ushort f, out ushort l)));
                pl.Add(simResult);
            }
            ushort sp1 = spine[1];
            yield return StartCoroutine(OnSim(() =>
            {
                var r = Chain("Power Line", Pts(w2, w1, 48), 0, 0, out ushort f, out ushort a);
                if (r != "ok") return "turbines: " + r;
                r = Chain("Power Line", Pts(w1, wt, 48), a, 0, out f, out ushort b);
                if (r != "ok") return "turbine-tower: " + r;
                return sp1 == 0 ? "tower-spine: no spine node" : Chain("Power Line", Pts(wt, Singleton<NetManager>.instance.m_nodes.m_buffer[sp1].m_position, 48), b, sp1, out f, out a);
            })); pl.Add(simResult);
            // water: the main pipe along the main road with a node at every side road, a branch along each side road, the tower's pipe node
            // (its prefab's pipe stub: Building.FindNode, layer WaterPipes) -> the main pipe's node at the second side road
            var mainAt = new[] { 60f, 136f, 232f, 328f, 424f, 500f }; var main = new ushort[mainAt.Length];
            yield return StartCoroutine(OnSim(() =>
            {
                ushort prev = 0;
                for (int i = 0; i + 1 < mainAt.Length; i++)
                {
                    ushort f, l; var r = Road("Water Pipe", P0 + Dir * mainAt[i], P0 + Dir * mainAt[i + 1], prev, 0, out f, out l);
                    if (r != "ok") return "main pipe: " + r;
                    if (i == 0) main[0] = f; main[i + 1] = l; prev = l;
                }
                return "ok";
            })); pl.Add(simResult);
            for (int k = 1; k <= 4; k++)
                foreach (var sgn in new[] { 1, -1 })
                {
                    ushort jn = main[k]; var j = P0 + Dir * mainAt[k];
                    if (jn == 0) { pl.Add("branch " + k + ": no main pipe node"); continue; }
                    yield return StartCoroutine(OnSim(() => { ushort f, l; return Road("Water Pipe", Singleton<NetManager>.instance.m_nodes.m_buffer[jn].m_position, j + Side * sgn * 150, jn, 0, out f, out l); }));
                    pl.Add(simResult);
                }
            ushort tnode = ids[2] == 0 ? (ushort)0 : bm.m_buildings.m_buffer[ids[2]].FindNode(ItemClass.Service.Water, ItemClass.SubService.None, ItemClass.Layer.WaterPipes, wt);
            cur["tower_pipe_node"] = tnode != 0;
            ushort m2 = main[2];
            yield return StartCoroutine(OnSim(() =>
            {
                if (m2 == 0) return "tower pipe: no main pipe node";
                var nm = Singleton<NetManager>.instance; ushort f, l;
                return Road("Water Pipe", tnode != 0 ? nm.m_nodes.m_buffer[tnode].m_position : wt, nm.m_nodes.m_buffer[m2].m_position, tnode, m2, out f, out l);
            })); pl.Add(simResult);
            cur["placed"] = placed; cur["networks"] = pl;
        }

        // points from a to b at most `step` apart (both ends included)
        static Vector3[] Pts(Vector3 a, Vector3 b, float step)
        {
            int n = Math.Max(1, Mathf.CeilToInt(Vector3.Distance(new Vector3(a.x, 0, a.z), new Vector3(b.x, 0, b.z)) / step));
            var r = new Vector3[n + 1]; for (int i = 0; i <= n; i++) r[i] = Vector3.Lerp(a, b, (float)i / n);
            return r;
        }
        // straight segments through pts, each its own CreateNode call so the nodes in between exist and the pieces join; startNode /
        // endNode join existing nodes -> "ok" or the first error, first / last node of the chain
        static string Chain(string info, Vector3[] pts, ushort startNode, ushort endNode, out ushort first, out ushort last)
        {
            first = last = 0; ushort prev = startNode;
            for (int i = 0; i + 1 < pts.Length; i++)
            {
                ushort f, l; var r = Road(info, pts[i], pts[i + 1], prev, i + 2 == pts.Length ? endNode : (ushort)0, out f, out l);
                if (r != "ok") return r + " (piece " + i + ")";
                if (i == 0) first = f; last = l; prev = l;
            }
            return "ok";
        }

        // ---- growth: ~6 in-game weeks at speed 3, then population, buildings, traffic, money, utilities
        IEnumerator Growth()
        {
            var bm = Singleton<BuildingManager>.instance; long cash0 = Cash; var t0 = Sim.m_currentGameTime;
            yield return StartCoroutine(FastSim()); cur["_turbo"] = turboHow;
            var samples = new ArrayList(); float w0 = Time.realtimeSinceStartup;
            int maxVeh = 0, maxInst = 0;
            while ((Sim.m_currentGameTime - t0).TotalDays < 42 && Time.realtimeSinceStartup - w0 < 420)
            {
                yield return new WaitForSeconds(5f);
                maxVeh = Math.Max(maxVeh, Singleton<VehicleManager>.instance.m_vehicleCount); maxInst = Math.Max(maxInst, Singleton<CitizenManager>.instance.m_instanceCount);
                samples.Add(new ArrayList { J.R((Sim.m_currentGameTime - t0).TotalDays, 1), (int)D0.m_populationData.m_finalCount });
            }
            Sim.SimulationPaused = true; yield return Frames(10);
            int zoned = 0, res = 0, com = 0, ind = 0, noPower = 0, noWater = 0, noRoad = 0, abandoned = 0;
            var svc = new ArrayList();                                    // service buildings with a problem (diagnostic, not compared)
            for (int i = 1; i < bm.m_buildings.m_size; i++)
            {
                var b = bm.m_buildings.m_buffer[i]; if ((b.m_flags & Building.Flags.Created) == 0 || b.Info == null) continue;
                var s = b.Info.m_class.m_service;
                if (s != ItemClass.Service.Residential && s != ItemClass.Service.Commercial && s != ItemClass.Service.Industrial)
                {
                    if (b.Info.m_buildingAI is PlayerBuildingAI && b.m_problems.IsNotNone && svc.Count < 12) svc.Add(b.Info.name + ": " + b.m_problems);
                    continue;
                }
                zoned++; if (s == ItemClass.Service.Residential) res++; else if (s == ItemClass.Service.Commercial) com++; else ind++;
                if ((b.m_problems & Notification.Problem1.Electricity).IsNotNone) noPower++;
                if ((b.m_problems & Notification.Problem1.Water).IsNotNone) noWater++;
                if ((b.m_problems & Notification.Problem1.RoadNotConnected).IsNotNone) noRoad++;
                if ((b.m_flags & Building.Flags.Abandoned) != 0) abandoned++;
            }
            var d = D0; var cur_ = cur;
            cur_["game_days"] = J.R((Sim.m_currentGameTime - t0).TotalDays, 1); cur_["wall_s"] = J.R(Time.realtimeSinceStartup - w0, 0);
            cur_["population"] = (int)d.m_populationData.m_finalCount; cur_["households"] = (int)d.m_residentialData.m_finalHomeOrWorkCount;
            cur_["zoned_buildings"] = zoned; cur_["res_buildings"] = res; cur_["com_buildings"] = com; cur_["ind_buildings"] = ind;
            cur_["no_power"] = noPower; cur_["no_water"] = noWater; cur_["no_road"] = noRoad; cur_["abandoned"] = abandoned;
            cur_["power_capacity"] = d.GetElectricityCapacity(); cur_["power_use"] = d.GetElectricityConsumption();
            cur_["water_capacity"] = d.GetWaterCapacity(); cur_["water_use"] = d.GetWaterConsumption(); cur_["garbage_capacity"] = d.GetGarbageCapacity();
            cur_["max_vehicles"] = maxVeh; cur_["max_citizen_instances"] = maxInst; cur_["cash_change"] = (Cash - cash0) / 100;
            cur_["income_acc"] = d.GetIncomeAccumulation(); cur_["samples"] = samples; cur_["_service_problems"] = svc;
        }

        // ---- save: the game's autosave path with a fixed name
        IEnumerator Save()
        {
            var sp = UIView.library.Get<SavePanel>("SavePanel");
            sp.AutoSave("gaprobe"); yield return new WaitForSeconds(1f);
            float t = Time.realtimeSinceStartup;
            while (SavePanel.isSaving && Time.realtimeSinceStartup - t < 60) yield return new WaitForSeconds(0.5f);
            cur["saved"] = !SavePanel.isSaving; cur["segments"] = Singleton<NetManager>.instance.m_segmentCount;
            // the marker run2 compares with: the city as saved (was written at the end of growth before transport existed)
            File.WriteAllText(Path.Combine(Path.GetDirectoryName(outPath), "marker.txt"), Singleton<NetManager>.instance.m_segmentCount + " " + ZonedCount() + " " + (int)D0.m_populationData.m_finalCount + " " + Cash / 100);
        }

        // ---- fixture: the grown city saved as "gafixture" + its road layout (P0, Dir) for the groups that run on it later
        IEnumerator Fixture()
        {
            var sp = UIView.library.Get<SavePanel>("SavePanel");
            sp.AutoSave("gafixture"); yield return new WaitForSeconds(1f);
            float t = Time.realtimeSinceStartup;
            while (SavePanel.isSaving && Time.realtimeSinceStartup - t < 60) yield return new WaitForSeconds(0.5f);
            var inv = System.Globalization.CultureInfo.InvariantCulture;
            File.WriteAllText(Path.Combine(Path.GetDirectoryName(outPath), "fixture.cfg"),
                              string.Join(" ", new[] { P0.x, P0.y, P0.z, Dir.x, Dir.z }.Select(v => v.ToString("R", inv)).ToArray()));
            cur["saved"] = !SavePanel.isSaving; cur["population"] = (int)D0.m_populationData.m_finalCount; cur["segments"] = (int)Singleton<NetManager>.instance.m_segmentCount;
        }

        // ---- faster than speed 3 if the game allows it (see the header); tried once per process, reapplied by later groups
        string turboHow; Action turboApply;
        IEnumerator FastSim()
        {
            Sim.SelectedSimulationSpeed = 3; Sim.SimulationPaused = false;
            if (!turboOn) { turboHow = "off"; yield break; }
            if (turboApply != null) { try { turboApply(); } catch { } yield break; }
            if (turboHow != null) yield break;
            turboHow = "no gain";
            yield return new WaitForSeconds(1f);
            uint f0 = Sim.m_currentFrameIndex; yield return new WaitForSeconds(2f); double r3 = (Sim.m_currentFrameIndex - f0) / 2.0;
            var tries = new List<KeyValuePair<string, Action>>();
            var prop = typeof(SimulationManager).GetProperty("SelectedSimulationSpeed", ANY);
            if (prop != null && prop.CanWrite) foreach (var v in new[] { 9, 6 }) { int vv = v; tries.Add(new KeyValuePair<string, Action>("SelectedSimulationSpeed=" + vv, () => prop.SetValue(Sim, vv, null))); }
            foreach (var f in typeof(SimulationManager).GetFields(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic))
            {
                if (f.FieldType != typeof(int) || f.IsInitOnly || !f.Name.ToLower().Contains("speed") || Convert.ToInt32(f.GetValue(Sim)) != 3) continue;
                foreach (var v in new[] { 9, 6 }) { var ff = f; int vv = v; tries.Add(new KeyValuePair<string, Action>(ff.Name + "=" + vv, () => ff.SetValue(Sim, vv))); }
            }
            foreach (var tr in tries)
            {
                try { tr.Value(); } catch { continue; }
                yield return new WaitForSeconds(1f);
                f0 = Sim.m_currentFrameIndex; yield return new WaitForSeconds(2f); double r = (Sim.m_currentFrameIndex - f0) / 2.0;
                if (r > r3 * 1.3) { turboApply = tr.Value; turboHow = tr.Key + " x" + Math.Round(r / Math.Max(r3, 0.1), 1); yield break; }
                Sim.SelectedSimulationSpeed = 3;
            }
        }

        static int ZonedCount()
        {
            int zoned = 0; var bm = Singleton<BuildingManager>.instance;
            for (int i = 1; i < bm.m_buildings.m_size; i++)
            {
                var b = bm.m_buildings.m_buffer[i]; if ((b.m_flags & Building.Flags.Created) == 0 || b.Info == null) continue;
                var s = b.Info.m_class.m_service; if (s == ItemClass.Service.Residential || s == ItemClass.Service.Commercial || s == ItemClass.Service.Industrial) zoned++;
            }
            return zoned;
        }

        // ---------------------------------------------------------------- reflection helpers (members not checked against the assemblies)
        const BindingFlags ANY = BindingFlags.Instance | BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic;
        static object Fld(object o, Type t, string name)
        {
            var f = t.GetField(name, ANY); if (f != null) return f.IsLiteral ? f.GetRawConstantValue() : f.GetValue(o);
            var p = t.GetProperty(name, ANY); return p != null ? p.GetValue(o, null) : null;
        }
        static bool SetFld(object o, Type t, string name, object v)
        {
            var f = t.GetField(name, ANY); if (f == null || f.IsLiteral || f.IsInitOnly) return false;
            f.SetValue(o, Convert.ChangeType(v, f.FieldType)); return true;
        }
        static object Call(object o, Type t, string name, object[] args)
        {
            foreach (var m in t.GetMethods(ANY)) if (m.Name == name && m.GetParameters().Length == args.Length) return m.Invoke(o, args);
            throw new MissingMethodException(t.Name, name);
        }
        // an instance method of the struct TransportLine on m_lines.m_buffer[line]: invoked on a boxed copy, then the fields the call
        // changed through `this` are written back (fields it changed through the buffer itself are kept)
        static object CallLine(ushort line, string name, object[] args)
        {
            var buf = Singleton<TransportManager>.instance.m_lines.m_buffer;
            object before = buf[line], box = buf[line];
            var r = Call(box, typeof(TransportLine), name, args);
            object now = buf[line];
            foreach (var f in typeof(TransportLine).GetFields(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic))
                if (!Equals(f.GetValue(box), f.GetValue(before))) f.SetValue(now, f.GetValue(box));
            buf[line] = (TransportLine)now;
            return r;
        }

        // ---- daynight: the day / night cycle runs (game hours per simulation frame), and noon / midnight light the city as vanilla:
        //      m_isNightTime, the brightest directional light (the sun / moon light of the day-night cycle), the ambient light
        uint dayFrames; Type SimT { get { return typeof(SimulationManager); } }
        float Hour() { var h = Fld(Sim, SimT, "m_currentDayTimeHour"); return h == null ? -1f : Convert.ToSingle(h); }
        static float SunNow()
        {
            float m = 0; foreach (var l in FindObjectsOfType<Light>()) if (l != null && l.enabled && l.type == LightType.Directional && l.gameObject.activeInHierarchy) m = Math.Max(m, l.intensity);
            return m;
        }
        static double Ambient() { var c = RenderSettings.ambientLight; return J.R((c.r + c.g + c.b) / 3f * RenderSettings.ambientIntensity, 3); }
        IEnumerator AtHour(float hour)
        {
            uint want = (uint)(hour / 24f * dayFrames), now = Sim.m_currentFrameIndex % dayFrames;
            SetFld(Sim, SimT, "m_dayTimeOffsetFrames", (want + dayFrames - now) % dayFrames);
            Sim.SelectedSimulationSpeed = 1; Sim.SimulationPaused = false; yield return new WaitForSeconds(0.5f); Sim.SimulationPaused = true;
            yield return new WaitForSeconds(2.5f);                     // the lights follow the (now fixed) time of day
        }
        IEnumerator DayNight()
        {
            cur["enabled"] = Fld(Sim, SimT, "m_enableDayNight");
            var df = Fld(null, SimT, "DAYTIME_FRAMES"); dayFrames = df == null ? 0u : Convert.ToUInt32(df);
            cur["daytime_frames"] = (long)dayFrames;
            if (dayFrames == 0) { cur["error"] = "SimulationManager.DAYTIME_FRAMES not found"; yield break; }
            var off0 = Fld(Sim, SimT, "m_dayTimeOffsetFrames");
            Sim.SimulationPaused = false; Sim.SelectedSimulationSpeed = 1; yield return new WaitForSeconds(1f);
            uint f0 = Sim.m_currentFrameIndex; float h0 = Hour(); yield return new WaitForSeconds(5f);
            uint f1 = Sim.m_currentFrameIndex; float h1 = Hour(); Sim.SimulationPaused = true;
            float dh = h1 - h0; if (dh < 0) dh += 24f;
            cur["hours_per_frame"] = f1 > f0 ? Math.Round(dh / (f1 - f0), 7) : 0.0;
            yield return StartCoroutine(AtHour(12f));
            cur["noon_hour"] = J.R(Hour(), 0); cur["noon_night"] = Fld(Sim, SimT, "m_isNightTime"); cur["noon_sun"] = J.R(SunNow(), 3); cur["noon_ambient"] = Ambient();
            yield return StartCoroutine(AtHour(0.5f));
            cur["midnight_hour"] = J.R(Hour(), 0); cur["midnight_night"] = Fld(Sim, SimT, "m_isNightTime"); cur["midnight_sun"] = J.R(SunNow(), 3); cur["midnight_ambient"] = Ambient();
            if (off0 != null) SetFld(Sim, SimT, "m_dayTimeOffsetFrames", off0);   // back to the city's own time for the next groups
            Sim.SimulationPaused = false; yield return new WaitForSeconds(0.5f); Sim.SimulationPaused = true;
        }

        // ---- transport: a Bus Depot beside the main road (the side and facing that gets road access), a bus line with 4 stops along the
        //      main road (TransportManager.CreateLine + TransportLine.AddStop: the transport tool's routines), ~12 in-game days at speed
        //      3 in the grown city: buses on the line, passengers on board (peak over the samples)
        IEnumerator Transport()
        {
            var bm = Singleton<BuildingManager>.instance; var tm = Singleton<TransportManager>.instance; var vm = Singleton<VehicleManager>.instance;
            var bi = PrefabCollection<BuildingInfo>.FindLoaded("Bus Depot");
            if (bi == null) { cur["error"] = "no Bus Depot prefab"; yield break; }
            float ang0 = Mathf.Atan2(Dir.x, -Dir.z); ushort depot = 0; string how = "no try";
            int k = 0;
            foreach (var sgn in new[] { 1, -1 })
                foreach (var turn in new[] { 0f, Mathf.PI, Mathf.PI / 2, -Mathf.PI / 2 })
                {
                    if (depot != 0) break;
                    k++;
                    var pos = P0 + Dir * 60 + Side * sgn * (8 + bi.m_cellLength * 4); pos.y = H(pos); float an = ang0 + turn; ushort id = 0;
                    yield return StartCoroutine(OnSim(() => { var rnd = Sim.m_randomizer; ushort b; bool ok = bm.CreateBuilding(out b, ref rnd, bi, pos, an, 0, Sim.m_currentBuildIndex); if (ok) Sim.m_currentBuildIndex++; id = ok ? b : (ushort)0; return ok ? "ok" : "failed"; }));
                    if (id == 0) { how = "CreateBuilding failed"; continue; }
                    Sim.SelectedSimulationSpeed = 3; Sim.SimulationPaused = false; yield return new WaitForSeconds(3f); Sim.SimulationPaused = true;
                    if ((bm.m_buildings.m_buffer[id].m_problems & Notification.Problem1.RoadNotConnected).IsNotNone)
                    {
                        ushort rel = id; yield return StartCoroutine(OnSim(() => { bm.ReleaseBuilding(rel); return "ok"; })); how = "no road access in " + k + " tries";
                    }
                    else { depot = id; how = "try " + k; }
                }
            cur["depot"] = depot != 0 ? "ok" : how; cur["depot_try"] = k;
            var info = PrefabCollection<TransportInfo>.FindLoaded("Bus");
            if (info == null) { cur["error"] = "no Bus TransportInfo"; yield break; }
            ushort line = 0;
            yield return StartCoroutine(OnSim(() =>
            {
                var rnd = Sim.m_randomizer; var args = new object[] { (ushort)0, rnd, info, true };
                var ok = Call(tm, typeof(TransportManager), "CreateLine", args); line = Convert.ToUInt16(args[0]);
                if (!(ok is bool) || !(bool)ok || line == 0) return "CreateLine failed";
                int added = 0;
                foreach (var along in new[] { 90f, 190f, 290f, 390f })
                {
                    var p = P0 + Dir * along + Side * 6; p.y = H(p);
                    var r = CallLine(line, "AddStop", new object[] { line, -1, p, false });
                    if (r is bool && (bool)r) added++;
                }
                var first = P0 + Dir * 90 + Side * 6; first.y = H(first);           // the tool closes a line by a stop on the first one
                try { CallLine(line, "AddStop", new object[] { line, -1, first, false }); } catch { }
                return "stops " + added;
            }));
            cur["line"] = simResult;
            if (line == 0) yield break;
            yield return Frames(30);
            Func<string, object> lineCall = (m) => { try { return CallLine(line, m, new object[] { line }); } catch (Exception e) { return "ERR " + e.GetType().Name; } };
            cur["stops"] = lineCall("CountStops"); cur["complete"] = tm.m_lines.m_buffer[line].m_flags.ToString().Contains("Complete");
            yield return StartCoroutine(FastSim()); cur["_turbo"] = turboHow;
            int peakBuses = 0, peakPax = 0, peakCap = 0, perBus = -1; var t0 = Sim.m_currentGameTime; float w0 = Time.realtimeSinceStartup;
            while ((Sim.m_currentGameTime - t0).TotalDays < 12 && Time.realtimeSinceStartup - w0 < 180)
            {
                yield return new WaitForSeconds(4f);
                int buses = 0, pax = 0, cap = 0;
                for (int i = 1; i < vm.m_vehicles.m_size; i++)
                {
                    var v = vm.m_vehicles.m_buffer[i];
                    if ((v.m_flags & Vehicle.Flags.Created) == 0 || v.m_transportLine != line) continue;
                    buses++; pax += v.m_transferSize;
                    // 2026-11-15: the seats (BusAI.m_passengerCapacity of the bus's prefab): measurable with nobody on board (NegBus)
                    var bai = v.Info != null ? v.Info.m_vehicleAI as BusAI : null; int c = bai != null ? bai.m_passengerCapacity : 0;
                    cap += c; perBus = Math.Max(perBus, c);
                }
                peakBuses = Math.Max(peakBuses, buses); peakPax = Math.Max(peakPax, pax); peakCap = Math.Max(peakCap, cap);
            }
            Sim.SimulationPaused = true;
            cur["line_vehicles"] = lineCall("CountVehicles"); cur["buses_peak"] = peakBuses; cur["passengers"] = peakPax;
            cur["bus_capacity"] = peakCap; cur["bus_capacity_per_bus"] = perBus;      // sum over the line's buses (peak) / the largest bus
            cur["game_days"] = J.R((Sim.m_currentGameTime - t0).TotalDays, 1);
        }

        IEnumerator SaveLoad()
        {
            var m = File.ReadAllText(Path.Combine(Path.GetDirectoryName(outPath), "marker.txt")).Split(' ');
            int zoned = ZonedCount();
            yield return Frames(30);
            Emit("saveload", new Hashtable { { "segments_same", Singleton<NetManager>.instance.m_segmentCount.ToString() == m[0] }, { "zoned_same", zoned.ToString() == m[1] },
                { "population_same", ((int)D0.m_populationData.m_finalCount).ToString() == m[2] }, { "cash_diff", Math.Abs(Cash / 100 - long.Parse(m[3])) < 2000 },
                { "city", Sim.m_metaData.m_CityName } });
        }
    }
}
