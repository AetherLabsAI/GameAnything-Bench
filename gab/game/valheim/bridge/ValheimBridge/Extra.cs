// Bridge 0.2: probes, fixtures and actions added for the 40-task pilot (note/valheim-bridge-devlog.md, section 11).
// Only NEW eval_query names and NEW act names: everything that existed in 0.1.x behaves as before.
//   probes   read_zdo, locations, item_stats, status_effect_info, prefab_info, spawn_data, events, global_keys, player_data,
//            npc_talk, biome_finder
//   fixtures set_zdo, place_location, container_put, apply_damage, set_stats, set_global_key, portal_tag, set_fps
//   actions  use_item_on, speed_sample, sleep, deconstruct, repair, repair_items, sail, dismount
using System;
using System.Collections;
using System.Collections.Generic;
using System.Collections.Specialized;
using System.Reflection;
using HarmonyLib;
using UnityEngine;

namespace ValheimBridge
{
    public static class Extra
    {
        // ================================================================ generic reflection dump
        static readonly HashSet<string> SkipTypes = new HashSet<string> { "EffectList", "AnimationCurve", "Gradient", "Material", "Mesh", "Texture2D", "Sprite[]" };

        /// Public instance fields of an object as JSON-able values (primitives, enums, vectors, structs, lists; Unity objects -> name).
        public static object Dump(object o, int depth)
        {
            if (o == null) return null;
            var t = o.GetType();
            if (o is string || o is bool || o is int || o is long || o is short || o is uint || o is byte || o is ushort || o is ulong) return o;
            if (o is float) return (float)Math.Round((float)o, 4);
            if (o is double) return Math.Round((double)o, 4);
            if (t.IsEnum) return o.ToString();
            if (o is Vector3) return Query.V((Vector3)o);
            if (o is Vector2) { var v = (Vector2)o; return new[] { Query.R(v.x), Query.R(v.y) }; }
            if (o is Quaternion) return Query.V(((Quaternion)o).eulerAngles);
            if (o is Color) { var c = (Color)o; return new[] { Query.R(c.r), Query.R(c.g), Query.R(c.b), Query.R(c.a) }; }
            if (o is ZDOID) return o.ToString();
            if (o is UnityEngine.Object) { var uo = (UnityEngine.Object)o; return uo ? uo.name.Replace("(Clone)", "") : null; }
            if (o is Delegate || SkipTypes.Contains(t.Name)) return null;
            if (depth <= 0) return t.Name;
            if (o is IDictionary)
            {
                var d = new OrderedDictionary(); int n = 0;
                foreach (DictionaryEntry e in (IDictionary)o) { if (n++ >= 100) break; d[e.Key.ToString()] = Dump(e.Value, depth - 1); }
                return d;
            }
            if (o is IEnumerable)
            {
                var l = new ArrayList(); int n = 0;
                foreach (var x in (IEnumerable)o) { if (n++ >= 100) { l.Add("..."); break; } l.Add(Dump(x, depth - 1)); }
                return l;
            }
            return Fields(o, depth - 1, null);
        }

        public static OrderedDictionary Fields(object o, int depth, Func<FieldInfo, bool> keep)
        {
            var d = new OrderedDictionary();
            foreach (var f in o.GetType().GetFields(BindingFlags.Public | BindingFlags.Instance))
            {
                if (keep != null && !keep(f)) continue;
                if (SkipTypes.Contains(f.FieldType.Name) || typeof(Delegate).IsAssignableFrom(f.FieldType)) continue;
                object v; try { v = f.GetValue(o); } catch { continue; }
                var dv = Dump(v, depth);
                if (dv == null && v != null) continue;
                d[f.Name] = dv;
            }
            return d;
        }

        static OrderedDictionary SE(StatusEffect se)
        {
            if (se == null) return null;
            var d = Query.D("name", se.name, "label", Query.Loc(se.m_name), "type", se.GetType().Name);
            d["fields"] = Fields(se, 2, f => f.DeclaringType != typeof(ScriptableObject) && f.Name != "m_character");
            return d;
        }

        // ================================================================ ZDO
        static Dictionary<int, string> names;
        static string Variant(string s, int mode)
        {
            switch (mode)
            {
                case 0: return s;
                case 1: return s.ToLowerInvariant();
                case 2: return char.ToUpperInvariant(s[0]) + s.Substring(1);
                case 3: { var sb = new System.Text.StringBuilder(); foreach (var c in s) { if (char.IsUpper(c) && sb.Length > 0) sb.Append('_'); sb.Append(char.ToLowerInvariant(c)); } return sb.ToString(); }
                default: return s;
            }
        }
        /// Names for ZDO key hashes: ZDOVars fields (the string when one of a few spellings of the field name hashes to it,
        /// otherwise "ZDOVars.s_field") + names the caller passes.
        static Dictionary<int, string> Names(Dictionary<string, object> a)
        {
            if (names == null)
            {
                names = new Dictionary<int, string>();
                foreach (var f in typeof(ZDOVars).GetFields(BindingFlags.Public | BindingFlags.Static))
                {
                    if (f.FieldType != typeof(int) || !f.Name.StartsWith("s_")) continue;
                    int h; try { h = (int)f.GetValue(null); } catch { continue; }
                    string nm = f.Name.Substring(2), found = null;
                    for (int m = 0; m < 4 && found == null; m++) { var v = Variant(nm, m); if (v.GetStableHashCode() == h) found = v; }
                    if (!names.ContainsKey(h)) names[h] = found ?? ("ZDOVars." + f.Name);
                }
                foreach (var s in new[] { "items", "addedDefaultItems", "fuel", "queued", "accTime", "bakeTimer", "spawntime", "level", "health", "tamed", "owner", "ownerName", "creator", "support", "tag", "text", "StartTime", "content", "plantTime", "picked", "picked_time", "enabled", "stack", "quality", "variant", "crafterID", "crafterName", "durability", "worldLevel", "seed" })
                    if (!names.ContainsKey(s.GetStableHashCode())) names[s.GetStableHashCode()] = s;
            }
            var res = new Dictionary<int, string>(names);
            if (a.ContainsKey("names") && a["names"] is IList)
                foreach (var x in (IList)a["names"]) if (x != null) res[x.ToString().GetStableHashCode()] = x.ToString();
            return res;
        }

        static ZDO FindZdo(string id)
        {
            var nv = Query.FindById(id);
            if (nv != null) return nv.GetZDO();
            var all = Query.F<Dictionary<ZDOID, ZDO>>(ZDOMan.instance, "m_objectsByID");
            foreach (var kv in all) if (kv.Key.ToString() == id) return kv.Value;
            return null;
        }

        /// The object a probe / fixture is about: {id} or the nearest {prefab} (exact or *sub*) within r of x,z (default: the player).
        static ZNetView Target(Dictionary<string, object> a, bool need = true)
        {
            string id = MiniJson.Str(a, "id", null);
            ZNetView nv = null;
            if (id != null) nv = Query.FindById(id);
            else if (a.ContainsKey("prefab"))
            {
                var aa = new Dictionary<string, object>(a); aa["limit"] = 1; aa.Remove("names");
                if (!aa.ContainsKey("r")) aa["r"] = 50.0;
                var c = Query.Center(aa); string pf = MiniJson.Str(a, "prefab", ""); float r = (float)MiniJson.Num(aa, "r", 50); float best = 1e9f;
                foreach (var x in Query.Instances())
                {
                    if (!Query.Match(Utils.GetPrefabName(x.gameObject), pf)) continue;
                    float d = Vector3.Distance(x.transform.position, c);
                    if (d <= r && d < best) { best = d; nv = x; }
                }
            }
            if (nv == null && need) throw new WcpException(Wcp.INVALID_PARAMS, "no such object (give id, or prefab [+ x, z, r]) " + (id ?? MiniJson.Str(a, "prefab", "")));
            return nv;
        }

        static OrderedDictionary Bucket<T>(string field, ZDOID uid, Dictionary<int, string> nm, Func<T, object> conv)
        {
            var all = Traverse.Create(typeof(ZDOExtraData)).Field(field).GetValue<Dictionary<ZDOID, BinarySearchDictionary<int, T>>>();
            var d = new OrderedDictionary();
            BinarySearchDictionary<int, T> b;
            if (all == null || !all.TryGetValue(uid, out b)) return d;
            foreach (var kv in b) { string k; d[nm.TryGetValue(kv.Key, out k) ? k : "#" + kv.Key] = conv(kv.Value); }
            return d;
        }

        static string ReadZdo(Dictionary<string, object> a)
        {
            ZDO zdo = null; ZNetView nv = null;
            string id = MiniJson.Str(a, "id", null);
            if (id != null) zdo = FindZdo(id);
            else { nv = Target(a); zdo = nv.GetZDO(); }
            if (zdo == null) return Json.Obj("zdo", null, "error", "no ZDO " + id);
            var nm = Names(a); bool full = Bool(a, "full"); var uid = zdo.m_uid;
            var pf = ZNetScene.instance.GetPrefab(zdo.GetPrefab());
            var d = Query.D("id", uid.ToString(), "prefab", pf != null ? pf.name : "#" + zdo.GetPrefab(), "pos", Query.V(zdo.GetPosition()),
                            "rot", Query.V(zdo.GetRotation().eulerAngles), "owner", zdo.GetOwner(), "loaded", Query.FindById(uid.ToString()) != null);
            d["ints"] = Bucket<int>("s_ints", uid, nm, v => v);
            d["floats"] = Bucket<float>("s_floats", uid, nm, v => (float)Math.Round(v, 4));
            d["longs"] = Bucket<long>("s_longs", uid, nm, v => v);
            d["strings"] = Bucket<string>("s_strings", uid, nm, v => v == null ? null : (full || v.Length <= 600 ? v : v.Substring(0, 600) + "...(" + v.Length + ")"));
            d["vec3"] = Bucket<Vector3>("s_vec3", uid, nm, v => Query.V(v));
            d["quats"] = Bucket<Quaternion>("s_quats", uid, nm, v => Query.V(v.eulerAngles));
            d["bytes"] = Bucket<byte[]>("s_byteArrays", uid, nm, v => v == null ? null : (object)(full ? Convert.ToBase64String(v) : ("<" + v.Length + " bytes>")));
            try { var cn = ZDOExtraData.GetConnection(uid); if (cn != null) d["connection"] = Query.D("type", cn.m_type.ToString(), "target", cn.m_target.ToString()); } catch { }
            if (Bool(a, "components") && (nv = nv ?? Query.FindById(uid.ToString())) != null) d["components"] = ComponentDump(nv.gameObject, a, 2);
            return Json.Val(d);
        }

        static string SetZdo(Dictionary<string, object> a)
        {
            var nv = Target(a); var z = nv.GetZDO(); string key = MiniJson.Str(a, "key", ""); string type = MiniJson.Str(a, "type", "float");
            if (key == "") throw new WcpException(Wcp.INVALID_PARAMS, "set_zdo needs key, value, type (int|float|long|string|bool|vec3)");
            nv.ClaimOwnership(); int h = key.GetStableHashCode(); var v = a.ContainsKey("value") ? a["value"] : null;
            switch (type)
            {
                case "int": z.Set(h, Convert.ToInt32(v)); break;
                case "float": z.Set(h, Convert.ToSingle(v)); break;
                case "long": z.Set(h, Convert.ToInt64(v)); break;
                case "bool": z.Set(h, v is bool && (bool)v); break;
                case "string": z.Set(h, v == null ? "" : v.ToString()); break;
                case "vec3": { var l = (IList)v; z.Set(h, new Vector3(Convert.ToSingle(l[0]), Convert.ToSingle(l[1]), Convert.ToSingle(l[2]))); break; }
                case "now_ticks": z.Set(h, ZNet.instance.GetTime().Ticks + (long)(Convert.ToDouble(v ?? 0.0) * 1e7)); break;   // world time + value seconds
                default: throw new WcpException(Wcp.INVALID_PARAMS, "type: int|float|long|string|bool|vec3|now_ticks");
            }
            return Json.Obj("success", true, "id", z.m_uid.ToString(), "key", key, "type", type);
        }

        // ================================================================ components / prefabs
        static OrderedDictionary ComponentDump(GameObject go, Dictionary<string, object> a, int depth)
        {
            var want = a.ContainsKey("component") ? MiniJson.Str(a, "component", null) : null;
            var d = new OrderedDictionary();
            foreach (var c in go.GetComponentsInChildren<MonoBehaviour>(true))
            {
                if (c == null) continue; var tn = c.GetType().Name;
                if (want != null && !string.Equals(tn, want, StringComparison.OrdinalIgnoreCase)) continue;
                if (want == null && (tn == "ZNetView" || tn == "ZSyncTransform" || tn == "LODGroup" || tn.StartsWith("Light") || tn == "ParticleSystemRenderer")) continue;
                string k = tn; int i = 2; while (d.Contains(k)) k = tn + "#" + i++;
                d[k] = Fields(c, depth, f => !f.Name.StartsWith("m_nview"));
            }
            return d;
        }

        static GameObject PrefabByName(string n)
        {
            GameObject go = ZNetScene.instance != null ? ZNetScene.instance.GetPrefab(n) : null;
            if (go == null && ObjectDB.instance != null) go = ObjectDB.instance.GetItemPrefab(n);
            return go;
        }

        static string PrefabInfo(Dictionary<string, object> a)
        {
            GameObject go; bool live = false;
            if (a.ContainsKey("id")) { var nv = Target(a); go = nv.gameObject; live = true; }
            else { go = PrefabByName(MiniJson.Str(a, "prefab", "")); if (go == null) return Json.Obj("prefab", null, "error", "no prefab " + MiniJson.Str(a, "prefab", "")); }
            return Json.Obj("prefab", Utils.GetPrefabName(go), "live", live, "components", ComponentDump(go, a, (int)MiniJson.Num(a, "depth", 2)));
        }

        // ================================================================ items
        static string ItemStats(Dictionary<string, object> a)
        {
            string n = MiniJson.Str(a, "item", MiniJson.Str(a, "prefab", "")); int q = (int)MiniJson.Num(a, "quality", 1);
            var go = ObjectDB.instance.GetItemPrefab(n);
            var idr = go != null ? go.GetComponent<ItemDrop>() : null;
            if (idr == null) return Json.Obj("item", null, "error", "no item prefab '" + n + "'");
            var it = idr.m_itemData.Clone(); it.m_quality = q; it.m_dropPrefab = go; var s = it.m_shared;
            float wl = 0; try { wl = Game.m_worldLevel; } catch { }
            var dmg = it.GetDamage(q, wl);
            var d = Query.D("prefab", go.name, "name", s.m_name, "label", Query.Loc(s.m_name), "type", s.m_itemType.ToString(), "quality", q,
                            "armor", Query.R(it.GetArmor(q, wl)), "block_power", Query.R(it.GetBaseBlockPower(q)), "deflection_force", Query.R(it.GetDeflectionForce(q)),
                            "timed_block_bonus", Query.R(s.m_timedBlockBonus), "damage", Dump(dmg, 1), "total_damage", Query.R(dmg.GetTotalDamage()),
                            "max_durability", Query.R(it.GetMaxDurability(q)), "weight", Query.R(s.m_weight), "movement_modifier", Query.R(s.m_movementModifier),
                            "max_stack", s.m_maxStackSize, "max_quality", s.m_maxQuality, "value", s.m_value, "tool_tier", s.m_toolTier,
                            "food", Query.D("health", s.m_food, "stamina", s.m_foodStamina, "eitr", s.m_foodEitr, "burn_time", s.m_foodBurnTime, "regen", s.m_foodRegen),
                            "set", Query.D("name", s.m_setName, "size", s.m_setSize, "status_effect", SE(s.m_setStatusEffect)),
                            "equip_status_effect", SE(s.m_equipStatusEffect), "consume_status_effect", SE(s.m_consumeStatusEffect),
                            "attack_status_effect", SE(s.m_attackStatusEffect), "damage_modifiers", Dump(s.m_damageModifiers, 2));
            var r = Query.FindRecipe(go.name); if (r != null) d["recipe"] = Query.RecipeInfo(r, Query.Me());
            if (!Bool(a, "brief")) d["shared"] = Fields(s, 2, f => f.Name != "m_icons" && f.Name != "m_buildPieces");
            return Json.Val(d);
        }

        static string StatusEffectInfo(Dictionary<string, object> a)
        {
            string n = MiniJson.Str(a, "name", "");
            var se = ObjectDB.instance.GetStatusEffect(n.GetStableHashCode());
            if (se == null) foreach (var x in ObjectDB.instance.m_StatusEffects) if (x != null && (x.name == n || x.m_name == n)) { se = x; break; }
            return Json.Obj("status_effect", SE(se));
        }

        // ================================================================ world: locations, biomes, spawning, events, keys
        static string Locations(Dictionary<string, object> a)
        {
            var zs = ZoneSystem.instance; string f = MiniJson.Str(a, "name", null);
            var c = Query.Center(a); float r = (float)MiniJson.Num(a, "r", 1e9); int limit = (int)MiniJson.Num(a, "limit", 50);
            var hits = new List<KeyValuePair<float, ZoneSystem.LocationInstance>>();
            foreach (var li in zs.m_locationInstances.Values)
            {
                string pn = li.m_location != null ? li.m_location.m_prefabName ?? li.m_location.m_name : null;
                if (pn == null || (f != null && !Query.Match(pn, f.Contains("*") ? f : f))) continue;
                if (Bool(a, "placed_only") && !li.m_placed) continue;
                var d0 = li.m_position - c; d0.y = 0; float dist = d0.magnitude;
                if (dist <= r) hits.Add(new KeyValuePair<float, ZoneSystem.LocationInstance>(dist, li));
            }
            hits.Sort((x, y) => x.Key.CompareTo(y.Key));
            var l = new ArrayList();
            for (int i = 0; i < hits.Count && i < limit; i++)
            {
                var li = hits[i].Value;
                l.Add(Query.D("name", li.m_location.m_prefabName, "pos", Query.V(li.m_position), "dist", Query.R(hits[i].Key), "placed", li.m_placed,
                              "biome", WorldGenerator.instance != null ? WorldGenerator.instance.GetBiome(li.m_position).ToString() : null));
            }
            var d = Query.D("count", hits.Count, "instances", l);
            var loaded = new ArrayList();
            foreach (var loc in Traverse.Create(typeof(Location)).Field("s_allLocations").GetValue<List<Location>>() ?? new List<Location>())
            {
                if (loc == null) continue; string pn = Utils.GetPrefabName(loc.gameObject);
                if (f != null && !Query.Match(pn, f)) continue;
                loaded.Add(Query.D("name", pn, "pos", Query.V(loc.transform.position), "dist", Query.R(Vector3.Distance(loc.transform.position, c)), "no_build", loc.m_noBuild));
            }
            d["loaded"] = loaded;
            if (Bool(a, "types"))
            {
                var tl = new ArrayList();
                foreach (var zl in zs.m_locations)
                {
                    string pn = zl.m_prefabName ?? zl.m_name;
                    if (f != null && pn != null && !Query.Match(pn, f)) continue;
                    tl.Add(Query.D("name", pn, "enabled", zl.m_enable, "quantity", zl.m_quantity, "biome", zl.m_biome.ToString(), "biome_area", zl.m_biomeArea.ToString(),
                                   "unique", zl.m_unique, "prioritized", zl.m_prioritized, "min_distance", zl.m_minDistance, "max_distance", zl.m_maxDistance,
                                   "min_altitude", zl.m_minAltitude, "max_altitude", zl.m_maxAltitude, "group", zl.m_group, "valid", zl.m_prefab.IsValid));
                }
                d["types"] = tl;
            }
            return Json.Val(d);
        }

        static string PlaceLocation(Dictionary<string, object> a)
        {
            var me = Query.MeOrThrow(); string n = MiniJson.Str(a, "name", "");
            var zs = ZoneSystem.instance; var loc = (ZoneSystem.ZoneLocation)AccessTools.Method(typeof(ZoneSystem), "GetLocation", new[] { typeof(string) }).Invoke(zs, new object[] { n });
            if (loc == null) return Json.Obj("success", false, "error", "no location type '" + n + "' (locations types=true lists them)");
            Vector3 pos = a.ContainsKey("x") ? Query.Center(a) : me.transform.position + me.transform.forward * 10f;
            float rad = Mathf.Max(loc.m_exteriorRadius, loc.m_interiorRadius);
            var zp = ZoneSystem.GetZonePos(ZoneSystem.GetZone(pos));
            var want = new Vector3(Mathf.Clamp(pos.x, zp.x - 32f + rad, zp.x + 32f - rad), pos.y, Mathf.Clamp(pos.z, zp.z - 32f + rad, zp.z + 32f - rad));
            bool save = !(a.ContainsKey("save") && a["save"] is bool && !(bool)a["save"]);     // default: keep world saving on (templates)
            bool ok = zs.TestSpawnLocation(n, pos, !save);
            return Json.Obj("success", ok, "name", n, "pos", Query.V(want), "radius", Query.R(rad), "clamped", Vector3.Distance(new Vector3(pos.x, 0, pos.z), new Vector3(want.x, 0, want.z)) > 0.01f,
                            "note", "the location is centred in its 64 m zone if it would not fit; rotation is random");
        }

        static string BiomeFinder(Dictionary<string, object> a)
        {
            var wg = WorldGenerator.instance; if (wg == null) throw new WcpException(Wcp.NOT_READY, "no world");
            string bs = MiniJson.Str(a, "biome", "Meadows"); Heightmap.Biome want;
            try { want = (Heightmap.Biome)Enum.Parse(typeof(Heightmap.Biome), bs, true); } catch { throw new WcpException(Wcp.INVALID_PARAMS, "biome: Meadows|BlackForest|Swamp|Mountain|Plains|Mistlands|AshLands|DeepNorth|Ocean"); }
            var c = Query.Center(a); float maxR = (float)MiniJson.Num(a, "max_r", 6000), step = (float)MiniJson.Num(a, "step", 48); int n = (int)MiniJson.Num(a, "n", 5);
            float minAbove = (float)MiniJson.Num(a, "min_above_water", want == Heightmap.Biome.Ocean ? -1000 : 1.0), maxAbove = (float)MiniJson.Num(a, "max_above_water", want == Heightmap.Biome.Ocean ? -2 : 1000);
            bool interior = !(a.ContainsKey("interior") && a["interior"] is bool && !(bool)a["interior"]); float slope = (float)MiniJson.Num(a, "max_slope", want == Heightmap.Biome.Ocean ? 100 : 1.5);
            float water = ZoneSystem.instance.m_waterLevel; var l = new ArrayList(); int tested = 0;
            for (float r = 0; r <= maxR && l.Count < n; r += step)
            {
                int k = r == 0 ? 1 : Mathf.Max(6, (int)(2 * Mathf.PI * r / step));
                for (int i = 0; i < k && l.Count < n; i++)
                {
                    float ang = i * 2 * Mathf.PI / k; float x = c.x + r * Mathf.Sin(ang), z = c.z + r * Mathf.Cos(ang); tested++;
                    if (wg.GetBiome(x, z) != want) continue;
                    if (interior && wg.GetBiomeArea(new Vector3(x, 0, z)) != Heightmap.BiomeArea.Median) continue;
                    float h = wg.GetHeight(x, z);
                    if (h - water < minAbove || h - water > maxAbove) continue;
                    float dh = Mathf.Max(Mathf.Abs(wg.GetHeight(x + 2, z) - h), Mathf.Abs(wg.GetHeight(x, z + 2) - h));
                    if (dh > slope) continue;
                    bool close = false; foreach (OrderedDictionary p in l) { var pp = (float[])p["pos"]; if (Mathf.Abs(pp[0] - x) + Mathf.Abs(pp[2] - z) < 4 * step) close = true; }
                    if (close) continue;
                    l.Add(Query.D("pos", new[] { Query.R(x), Query.R(h), Query.R(z) }, "dist", Query.R(r), "above_water", Query.R(h - water)));
                }
            }
            return Json.Obj("biome", want.ToString(), "points", l, "tested", tested,
                            "note", "heights are the generator's (before terrain edits); teleport there, then use ground / free_spot for an exact spot");
        }

        static string SpawnData(Dictionary<string, object> a)
        {
            string pf = MiniJson.Str(a, "prefab", null), bs = MiniJson.Str(a, "biome", null);
            var lists = new List<SpawnSystemList>(); var seen = new HashSet<int>();
            var inst = Traverse.Create(typeof(SpawnSystem)).Field("m_instances").GetValue<List<SpawnSystem>>();
            var systems = new List<SpawnSystem>(inst ?? new List<SpawnSystem>());
            if (systems.Count == 0) { var zc = ZNetScene.instance.GetPrefab("_ZoneCtrl"); if (zc != null && zc.GetComponent<SpawnSystem>()) systems.Add(zc.GetComponent<SpawnSystem>()); }
            foreach (var s in systems) foreach (var sl in s.m_spawnLists) if (sl != null && seen.Add(sl.GetInstanceID())) lists.Add(sl);
            var l = new ArrayList();
            foreach (var sl in lists)
                foreach (var sd in sl.m_spawners)
                {
                    if (sd == null) continue; string pn = sd.m_prefab != null ? sd.m_prefab.name : null;
                    if (pf != null && (pn == null || !Query.Match(pn, pf))) continue;
                    if (bs != null && sd.m_biome.ToString().IndexOf(bs, StringComparison.OrdinalIgnoreCase) < 0) continue;
                    var d = Fields(sd, 1, null); d["list"] = sl.name; l.Add(d);
                }
            var local = new ArrayList();
            if (!(a.ContainsKey("local") && a["local"] is bool && !(bool)a["local"]))
            {
                var c = Query.Center(a); float r = (float)MiniJson.Num(a, "r", 64);
                foreach (var nv in Query.Instances())
                {
                    if (Vector3.Distance(nv.transform.position, c) > r) continue;
                    var cs = nv.GetComponent<CreatureSpawner>(); var sa = nv.GetComponent<SpawnArea>();
                    if (cs != null && (pf == null || (cs.m_creaturePrefab != null && Query.Match(cs.m_creaturePrefab.name, pf))))
                        local.Add(Query.D("kind", "CreatureSpawner", "id", nv.GetZDO().m_uid.ToString(), "pos", Query.V(nv.transform.position), "fields", Fields(cs, 1, null)));
                    if (sa != null) local.Add(Query.D("kind", "SpawnArea", "id", nv.GetZDO().m_uid.ToString(), "prefab", Utils.GetPrefabName(nv.gameObject), "pos", Query.V(nv.transform.position), "fields", Fields(sa, 2, null)));
                }
            }
            return Json.Obj("lists", lists.Count, "count", l.Count, "spawners", l, "local", local);
        }

        static string Events(Dictionary<string, object> a)
        {
            var rs = RandEventSystem.instance; if (rs == null) return Json.Obj("events", null);
            Func<RandomEvent, object> ev = e => e == null ? null : (object)Query.D("name", e.m_name, "pos", Query.V(e.m_pos), "time", Query.R(e.m_time), "duration", Query.R(e.m_duration),
                                                                                   "active", Query.F<bool>(e, "m_active"), "biome", e.m_biome.ToString());
            var defs = new ArrayList();
            if (!Bool(a, "brief"))
                foreach (var e in rs.m_events)
                {
                    var sp = new ArrayList(); foreach (var s in e.m_spawn) if (s != null && s.m_prefab != null) sp.Add(s.m_prefab.name);
                    defs.Add(Query.D("name", e.m_name, "enabled", e.m_enabled, "random", e.m_random, "duration", e.m_duration, "near_base_only", e.m_nearBaseOnly,
                                     "biome", e.m_biome.ToString(), "required_global_keys", e.m_requiredGlobalKeys, "not_required_global_keys", e.m_notRequiredGlobalKeys,
                                     "spawn", sp, "start_message", Query.Loc(e.m_startMessage)));
                }
            return Json.Obj("active", ev(Query.F<RandomEvent>(rs, "m_activeEvent")), "random", ev(Query.F<RandomEvent>(rs, "m_randomEvent")),
                            "forced", ev(Query.F<RandomEvent>(rs, "m_forcedEvent")), "timer", Query.R(Query.F<float>(rs, "m_eventTimer")),
                            "interval_min", rs.m_eventIntervalMin, "chance", rs.m_eventChance, "event_rate", Game.m_eventRate, "definitions", defs);
        }

        static string GlobalKeys(Dictionary<string, object> a)
        {
            var zs = ZoneSystem.instance; var p = Query.Me();
            var uniq = p != null ? new ArrayList(new List<string>(Query.F<HashSet<string>>(p, "m_uniques"))) : null;
            return Json.Obj("global_keys", zs.GetGlobalKeys(), "values", Dump(zs.m_globalKeysValues, 1), "player_unique_keys", uniq);
        }

        static string SetGlobalKey(Dictionary<string, object> a)
        {
            string k = MiniJson.Str(a, "key", ""); if (k == "") throw new WcpException(Wcp.INVALID_PARAMS, "set_global_key needs key [remove=true] [player=true]");
            bool rm = Bool(a, "remove");
            if (Bool(a, "player")) { var p = Query.MeOrThrow(); if (rm) p.RemoveUniqueKey(k); else p.AddUniqueKey(k); }
            else if (rm) ZoneSystem.instance.RemoveGlobalKey(k); else ZoneSystem.instance.SetGlobalKey(k);
            return GlobalKeys(a);
        }

        // ================================================================ player data, stats, NPC talk
        static string PlayerData(Dictionary<string, object> a)
        {
            var p = Query.MeOrThrow(); var inv = new ArrayList();
            foreach (var it in p.GetInventory().GetAllItems())
                if (it.m_customData != null && it.m_customData.Count > 0)
                    inv.Add(Query.D("prefab", it.m_dropPrefab != null ? it.m_dropPrefab.name : it.m_shared.m_name, "grid", new[] { it.m_gridPos.x, it.m_gridPos.y }, "custom_data", Dump(it.m_customData, 1)));
            var extra = new OrderedDictionary();      // Inventory fields of components on the player (mod backpacks, quivers, ...)
            foreach (var c in p.GetComponents<MonoBehaviour>())
            {
                if (c == null) continue;
                foreach (var f in c.GetType().GetFields(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance))
                {
                    if (f.FieldType != typeof(Inventory)) continue;
                    Inventory iv; try { iv = (Inventory)f.GetValue(c); } catch { continue; }
                    if (iv == null || iv == p.GetInventory()) continue;
                    extra[c.GetType().Name + "." + f.Name] = Query.InventoryOf(iv);
                }
            }
            return Json.Obj("custom_data", Dump(p.m_customData, 1), "unique_keys", new ArrayList(new List<string>(Query.F<HashSet<string>>(p, "m_uniques"))),
                            "items_with_custom_data", inv, "extra_inventories", extra, "known_stations", Dump(Query.F<object>(p, "m_knownStations"), 1),
                            "base_value", p.GetBaseValue(), "safe_in_home", p.InShelter(), "cold", p.GetSEMan().HaveStatusEffect("Cold".GetStableHashCode()));
        }

        static string SetStats(Dictionary<string, object> a)
        {
            var p = Query.MeOrThrow();
            if (a.ContainsKey("health")) p.SetHealth((float)MiniJson.Num(a, "health", p.GetHealth()));
            if (a.ContainsKey("stamina")) Traverse.Create(p).Field("m_stamina").SetValue((float)MiniJson.Num(a, "stamina", p.GetStamina()));
            if (a.ContainsKey("eitr")) Traverse.Create(p).Field("m_eitr").SetValue((float)MiniJson.Num(a, "eitr", p.GetEitr()));
            return Json.Obj("success", true, "health", Query.R(p.GetHealth()), "max_health", Query.R(p.GetMaxHealth()), "stamina", Query.R(p.GetStamina()), "eitr", Query.R(p.GetEitr()));
        }

        public class Talk { public float T; public string Prefab, Id, Topic, Text; public Vector3 Pos; }
        public static readonly List<Talk> Talks = new List<Talk>(); public static int TalkTotal;

        static string NpcTalk(Dictionary<string, object> a)
        {
            int n = (int)MiniJson.Num(a, "n", 20); float since = (float)MiniJson.Num(a, "since", -1); string pf = MiniJson.Str(a, "prefab", null);
            var l = new ArrayList();
            lock (Talks)
                for (int i = Math.Max(0, Talks.Count - n); i < Talks.Count; i++)
                {
                    var t = Talks[i]; if (t.T < since || (pf != null && !Query.Match(t.Prefab ?? "", pf))) continue;
                    l.Add(Query.D("time", Query.R(t.T), "prefab", t.Prefab, "id", t.Id, "pos", Query.V(t.Pos), "topic", t.Topic, "text", t.Text));
                }
            return Json.Obj("total", TalkTotal, "now", Query.R(Time.time), "talk", l);
        }

        // ================================================================ fixtures on objects
        static string ContainerPut(Dictionary<string, object> a)
        {
            var nv = Target(a); var go = nv.gameObject; string item = MiniJson.Str(a, "item", ""); int n = (int)MiniJson.Num(a, "amount", 1), q = (int)MiniJson.Num(a, "quality", 1);
            var ipf = ObjectDB.instance.GetItemPrefab(item);
            if (ipf == null) return Json.Obj("success", false, "error", "no item prefab '" + item + "'");
            nv.ClaimOwnership(); int added = 0; string kind;
            var ct = go.GetComponent<Container>(); var sm = go.GetComponent<Smelter>(); var fe = go.GetComponent<Fermenter>(); var ck = go.GetComponent<CookingStation>(); var fp = go.GetComponent<Fireplace>();
            if (ct != null)
            {
                kind = "container"; var inv = ct.GetInventory(); int max = ipf.GetComponent<ItemDrop>().m_itemData.m_shared.m_maxStackSize;
                while (added < n) { int k = Math.Min(n - added, Math.Max(1, max)); if (inv.AddItem(item, k, q, 0, 0L, "", false) == null) break; added += k; }
                return Json.Obj("success", added == n, "kind", kind, "added", added, "contents", Query.InventoryOf(inv));
            }
            if (sm != null)
            {
                bool fuel = sm.m_fuelItem != null && sm.m_fuelItem.gameObject.name == item;
                kind = fuel ? "smelter_fuel" : "smelter_ore"; bool force = Bool(a, "force");
                for (int i = 0; i < n; i++)
                {
                    if (fuel) { if (!force && nv.GetZDO().GetFloat(ZDOVars.s_fuel) > sm.m_maxFuel - 1) break; nv.InvokeRPC("RPC_AddFuel"); }
                    else { if (!force && (int)AccessTools.Method(typeof(Smelter), "GetQueueSize").Invoke(sm, null) >= sm.m_maxOre) break;
                           if (!(bool)AccessTools.Method(typeof(Smelter), "IsItemAllowed", new[] { typeof(string) }).Invoke(sm, new object[] { item })) return Json.Obj("success", false, "kind", kind, "error", item + " is not an input of " + Utils.GetPrefabName(go));
                           nv.InvokeRPC("RPC_AddOre", item, false); }
                    added++;
                }
                return Json.Obj("success", added == n, "kind", kind, "added", added, "fuel", Query.R(nv.GetZDO().GetFloat(ZDOVars.s_fuel)), "queued", nv.GetZDO().GetInt(ZDOVars.s_queued));
            }
            if (fe != null) { nv.InvokeRPC("RPC_AddItem", item.GetStableHashCode(), false); return Json.Obj("success", true, "kind", "fermenter", "added", 1, "note", "a fermenter takes one item; check read_zdo content"); }
            if (ck != null)
            {
                bool fuel = ck.m_fuelItem != null && ck.m_fuelItem.gameObject.name == item;
                for (int i = 0; i < n; i++) { if (fuel) nv.InvokeRPC("RPC_AddFuel"); else nv.InvokeRPC("RPC_AddItem", item, false); added++; }
                return Json.Obj("success", true, "kind", fuel ? "cooking_fuel" : "cooking_item", "added", added);
            }
            if (fp != null && fp.m_fuelItem != null && fp.m_fuelItem.gameObject.name == item)
            {
                float f0 = nv.GetZDO().GetFloat(ZDOVars.s_fuel); nv.GetZDO().Set(ZDOVars.s_fuel, Mathf.Min(fp.m_maxFuel, f0 + n));
                return Json.Obj("success", true, "kind", "fireplace_fuel", "fuel", Query.R(nv.GetZDO().GetFloat(ZDOVars.s_fuel)));
            }
            // anything else (mod machines, item stands, ...): give the item to the player and hand it to the object's Interactable.UseItem
            var me = Query.MeOrThrow(); var minv = me.GetInventory();
            foreach (var ia in go.GetComponentsInChildren<Interactable>())
            {
                for (int i = added; i < n; i++)
                {
                    var it = minv.AddItem(item, 1, q, 0, 0L, "", false); if (it == null) break;
                    bool used = false; try { used = ia.UseItem(me, it); } catch (Exception e) { Plugin.Instance.Warn("container_put UseItem: " + e.Message); }
                    if (!used) { minv.RemoveOneItem(it); break; }
                    if (minv.ContainsItem(it) && it.m_stack > 0) minv.RemoveOneItem(it);    // UseItem did not consume it (some only read the item)
                    added++;
                }
                if (added > 0) return Json.Obj("success", added == n, "kind", "interactable:" + ia.GetType().Name, "added", added);
            }
            return Json.Obj("success", false, "error", Utils.GetPrefabName(go) + " has no container, smelter, fermenter, cooking station or Interactable that takes " + item);
        }

        static string ApplyDamage(Dictionary<string, object> a)
        {
            GameObject go; var me = Query.Me();
            if (MiniJson.Str(a, "target", null) == "player" || (!a.ContainsKey("id") && !a.ContainsKey("prefab"))) go = Query.MeOrThrow().gameObject;
            else go = Target(a).gameObject;
            var dest = go.GetComponentInParent<IDestructible>();
            if (dest == null) return Json.Obj("success", false, "error", Utils.GetPrefabName(go) + " cannot be damaged (no IDestructible)");
            var hit = new HitData();
            var dm = MiniJson.Dict(a, "damage");
            if (dm.Count == 0 && a.ContainsKey("amount")) dm[MiniJson.Str(a, "type", "blunt")] = a["amount"];
            if (dm.Count == 0)        // damage types given as top-level arguments (vh q apply_damage id=... blunt=10)
                foreach (var k in new[] { "damage", "blunt", "slash", "pierce", "chop", "pickaxe", "fire", "frost", "lightning", "poison", "spirit" })
                    if (a.ContainsKey(k) && !(a[k] is Dictionary<string, object>)) dm[k] = a[k];
            object boxed = hit.m_damage;
            foreach (var kv in dm)
            {
                var f = typeof(HitData.DamageTypes).GetField("m_" + kv.Key.ToLowerInvariant());
                if (f == null) throw new WcpException(Wcp.INVALID_PARAMS, "damage type " + kv.Key + ": damage|blunt|slash|pierce|chop|pickaxe|fire|frost|lightning|poison|spirit");
                f.SetValue(boxed, Convert.ToSingle(kv.Value));
            }
            hit.m_damage = (HitData.DamageTypes)boxed;
            hit.m_toolTier = (short)MiniJson.Num(a, "tool_tier", 10); hit.m_pushForce = (float)MiniJson.Num(a, "push", 0);
            hit.m_staggerMultiplier = (float)MiniJson.Num(a, "stagger", 1); hit.m_blockable = Bool(a, "blockable"); hit.m_dodgeable = Bool(a, "dodgeable");
            var col = go.GetComponentInChildren<Collider>(); hit.m_point = col != null ? col.bounds.center : go.transform.position + Vector3.up;
            Character att = null; string ats = MiniJson.Str(a, "attacker", null);
            if (ats == "player") att = me; else if (ats != null) { var an = Query.FindById(ats); att = an != null ? an.GetComponent<Character>() : null; }
            if (att != null) { hit.SetAttacker(att); hit.m_dir = (hit.m_point - att.transform.position).normalized; } else hit.m_dir = Vector3.down;
            var nv = go.GetComponentInParent<ZNetView>(); object max0; var h0 = nv != null ? Query.Health(nv, out max0) : null;
            var ch = go.GetComponentInParent<Character>(); float hp0 = ch != null ? ch.GetHealth() : -1;
            dest.Damage(hit);
            object max1; var h1 = nv != null && nv.IsValid() ? Query.Health(nv, out max1) : null;
            return Json.Obj("success", true, "target", Utils.GetPrefabName(nv != null ? nv.gameObject : go), "id", nv != null && nv.GetZDO() != null ? nv.GetZDO().m_uid.ToString() : null,
                            "damage", Dump(hit.m_damage, 1), "health_before", h0, "health_after", h1, "destroyed", nv == null || !nv.IsValid(),
                            "note", "health_after is read in the same frame; damage RPCs, staggers and deaths settle over the next frames (re-read objects)");
        }

        static string PortalTag(Dictionary<string, object> a)
        {
            var nv = Target(a); var tw = nv.GetComponent<TeleportWorld>();
            if (tw == null) return Json.Obj("success", false, "error", "not a portal (TeleportWorld)");
            nv.ClaimOwnership(); tw.SetText(MiniJson.Str(a, "tag", ""));
            return Json.Obj("success", true, "id", nv.GetZDO().m_uid.ToString(), "tag", tw.GetText(), "note", "portals connect every few seconds (Game.ConnectPortals): read objects detail connected");
        }

        public static int FpsCap = -1; static float fpsCheck;
        static string SetFps(Dictionary<string, object> a)
        {
            FpsCap = (int)MiniJson.Num(a, "fps", -1); fpsCheck = 0; TickFps();
            if (FpsCap <= 0) Application.targetFrameRate = -1;          // back to uncapped (the game re-applies its own limit when it changes)
            return Json.Obj("fps_cap", FpsCap, "target_frame_rate", Application.targetFrameRate);
        }
        public static void TickFps()
        {
            if (FpsCap <= 0 || Time.realtimeSinceStartup - fpsCheck < 1f) return;
            fpsCheck = Time.realtimeSinceStartup;
            if (Application.targetFrameRate != FpsCap) Application.targetFrameRate = FpsCap;
            if (QualitySettings.vSyncCount != 0) QualitySettings.vSyncCount = 0;
        }

        // ================================================================ extra fields for objects (Query.Obj)
        public static void Describe(ZNetView nv, OrderedDictionary d, bool detail)
        {
            var go = nv.gameObject;
            var w = go.GetComponent<WearNTear>();
            if (w != null)
            {
                try
                {
                    d["support"] = Query.R((float)AccessTools.Method(typeof(WearNTear), "GetSupport").Invoke(w, null));
                    d["max_support"] = Query.R((float)AccessTools.Method(typeof(WearNTear), "GetMaxSupport").Invoke(w, null));
                    d["material"] = w.m_materialType.ToString();
                }
                catch { }
            }
            if (!detail) return;
            var pl = go.GetComponent<Plant>();
            if (pl != null) { try { d["plant"] = Query.D("status", Query.F<object>(pl, "m_status").ToString(), "grow_time", Query.R((float)AccessTools.Method(typeof(Plant), "GetGrowTime").Invoke(pl, null)),
                                                         "time_since_planted", Query.R((float)Convert.ToDouble(AccessTools.Method(typeof(Plant), "TimeSincePlanted").Invoke(pl, null)))); } catch { } }
            var tm = go.GetComponent<Tameable>();
            if (tm != null) { try { d["tame"] = Query.D("tamed", tm.IsTamed(), "hungry", tm.IsHungry(), "remaining_time", Query.R((float)AccessTools.Method(typeof(Tameable), "GetRemainingTime").Invoke(tm, null)),
                                                        "saddled", (bool)AccessTools.Method(typeof(Tameable), "HaveSaddle").Invoke(tm, null)); } catch { } }
            var tw = go.GetComponent<TeleportWorld>();
            if (tw != null)
            {
                d["portal"] = Query.D("tag", tw.GetText(), "forward", Query.V(tw.transform.forward), "connected", (bool)AccessTools.Method(typeof(TeleportWorld), "TargetFound").Invoke(tw, null),
                                      "trigger_pos", tw.GetComponentInChildren<TeleportWorldTrigger>() != null ? Query.V(tw.GetComponentInChildren<TeleportWorldTrigger>().transform.position) : null);
            }
            var sh = go.GetComponent<Ship>();
            if (sh != null)
            {
                var body = go.GetComponent<Rigidbody>();
                d["ship"] = Query.D("speed", Query.F<object>(sh, "m_speed").ToString(), "rudder", Query.R(Query.F<float>(sh, "m_rudderValue")),
                                    "velocity", body != null ? Query.R(body.linearVelocity.magnitude) : 0f, "players", Query.F<List<Player>>(sh, "m_players").Count,
                                    "helm_pos", sh.GetComponentInChildren<ShipControlls>() != null ? Query.V(sh.GetComponentInChildren<ShipControlls>().m_attachPoint.position) : null);
            }
            var sm = go.GetComponent<Smelter>();
            if (sm != null) d["smelter"] = Query.D("fuel", Query.R(nv.GetZDO().GetFloat(ZDOVars.s_fuel)), "queued", nv.GetZDO().GetInt(ZDOVars.s_queued),
                                                   "processed", nv.GetZDO().GetInt(ZDOVars.s_spawnAmount), "fuel_item", sm.m_fuelItem != null ? sm.m_fuelItem.gameObject.name : null,
                                                   "max_ore", sm.m_maxOre, "max_fuel", sm.m_maxFuel, "seconds_per_product", sm.m_secPerProduct);
            var bed = go.GetComponent<Bed>();
            if (bed != null) d["bed"] = Query.D("owner", (string)AccessTools.Method(typeof(Bed), "GetOwnerName").Invoke(bed, null), "current", bed.IsCurrent());
        }

        // ================================================================ dispatch
        static bool Bool(Dictionary<string, object> a, string k) { return a.ContainsKey(k) && a[k] is bool && (bool)a[k]; }

        public static string EvalQuery(string q, Dictionary<string, object> a)
        {
            switch (q)
            {
                case "read_zdo": return ReadZdo(a);
                case "set_zdo": return SetZdo(a);
                case "prefab_info": return PrefabInfo(a);
                case "item_stats": return ItemStats(a);
                case "status_effect_info": return StatusEffectInfo(a);
                case "locations": case "location_finder": return Locations(a);
                case "place_location": return PlaceLocation(a);
                case "biome_finder": return BiomeFinder(a);
                case "spawn_data": return SpawnData(a);
                case "events": return Events(a);
                case "global_keys": return GlobalKeys(a);
                case "set_global_key": return SetGlobalKey(a);
                case "player_data": return PlayerData(a);
                case "set_stats": return SetStats(a);
                case "npc_talk": return NpcTalk(a);
                case "container_put": return ContainerPut(a);
                case "apply_damage": return ApplyDamage(a);
                case "portal_tag": return PortalTag(a);
                case "set_fps": return SetFps(a);
                case "cover":
                {
                    var pt = a.ContainsKey("y") ? Query.Center(a) : Query.Center(a) + Vector3.up * (float)MiniJson.Num(a, "dy", 1.0);
                    float cov; bool roof; Cover.GetCoverForPoint(pt, out cov, out roof);
                    var p = Query.Me();
                    return Json.Obj("point", Query.V(pt), "cover", Query.R(cov), "under_roof", roof, "player_in_shelter", p != null && p.InShelter(),
                                    "heat", EffectArea.IsPointInsideArea(pt, EffectArea.Type.Heat, 1f) != null, "note", "beds need under_roof and cover >= 0.8; a workbench needs a roof (cover)");
                }
                case "tutorials":
                {
                    Raven.m_tutorialsEnabled = !(a.ContainsKey("enabled") && a["enabled"] is bool && !(bool)a["enabled"]);
                    var rv = Traverse.Create(typeof(Raven)).Field("m_instance").GetValue<Raven>();
                    bool removed = false;
                    if (!Raven.m_tutorialsEnabled && rv != null) { UnityEngine.Object.Destroy(rv.gameObject); removed = true; }
                    return Json.Obj("tutorials", Raven.m_tutorialsEnabled, "raven_removed", removed);
                }
            }
            return null;
        }

        public static readonly string[] QueryNames = { "read_zdo", "set_zdo", "prefab_info", "item_stats", "status_effect_info", "locations", "place_location", "biome_finder",
                                                       "spawn_data", "events", "global_keys", "set_global_key", "player_data", "set_stats", "npc_talk", "container_put", "apply_damage", "portal_tag", "set_fps", "tutorials", "cover" };

        static Hashtable T(string type) { return new Hashtable { { "type", type } }; }
        static Hashtable A(string name, string doc, Hashtable props, bool available, string reason)
        {
            var h = new Hashtable { { "name", name }, { "doc", doc }, { "args", new Hashtable { { "type", "object" }, { "properties", props } } }, { "available", available } };
            if (!available) h["reason"] = reason; return h;
        }

        public static void AddActions(ArrayList l, bool live, string why)
        {
            var target = new Hashtable { { "id", T("string") }, { "x", T("number") }, { "y", T("number") }, { "z", T("number") } };
            l.Add(A("use_item_on", "use an inventory item on an object (aim at id / point first, like pressing its hotbar key while hovering): fill a smelter/kiln, put an item on a stand, saddle a tame ...",
                    new Hashtable(target) { { "item", T("string") } }, live, why));
            l.Add(A("speed_sample", "measure the player's (or a creature's: id) horizontal speed over seconds; move: none|walk|run|back holds the keys meanwhile",
                    new Hashtable { { "id", T("string") }, { "seconds", T("number") }, { "move", T("string") }, { "yaw", T("number") } }, live, why));
            l.Add(A("sleep", "use a bed (claims it first if needed) and sleep; waits until morning or `wait` seconds", new Hashtable(target) { { "wait", T("number") } }, live, why));
            l.Add(A("deconstruct", "remove a build piece with the hammer (refunds its materials)", new Hashtable { { "id", T("string") } }, live, why));
            l.Add(A("repair", "repair a damaged build piece with the hammer", new Hashtable { { "id", T("string") } }, live, why));
            l.Add(A("repair_items", "repair all worn items at the open crafting station", new Hashtable(), live, why));
            l.Add(A("sail", "on a ship's helm (use it first): set speed stop|back|slow|half|full and rudder -1..1, then keep going for seconds",
                    new Hashtable { { "speed", T("string") }, { "rudder", T("number") }, { "seconds", T("number") } }, live, why));
            l.Add(A("mount", "use a ship's helm, a saddled tame's saddle or a chair by the object's id (aims at that part)", new Hashtable { { "id", T("string") }, { "reach", T("number") } }, live, why));
            l.Add(A("dismount", "stand up / get off (chair, bed, saddle, helm): presses jump", new Hashtable(), live, why));
        }

        public static bool Act(string name, Dictionary<string, object> a, Player me, Action<string> ok, Action<int, string> fail)
        {
            switch (name)
            {
                case "use_item_on":
                {
                    var it = Query.FindItem(me.GetInventory(), MiniJson.Str(a, "item", ""));
                    if (it == null) { fail(Wcp.INVALID_PARAMS, "no item '" + MiniJson.Str(a, "item", "") + "' in the inventory"); return true; }
                    string id = MiniJson.Str(a, "id", null); Vector3? aim = Aim(a, id, it);
                    if (aim == null) { fail(Wcp.INVALID_PARAMS, "use_item_on needs id or x, z"); return true; }
                    Control.Start(new UseItemOnAction(aim.Value, id, it, (float)MiniJson.Num(a, "reach", 1.5)) { Stand = StandFor(id, aim.Value) }, ok, fail); return true;
                }
                case "speed_sample":
                {
                    Character c = me; string id = MiniJson.Str(a, "id", null);
                    if (id != null) { var nv = Query.FindById(id); c = nv != null ? nv.GetComponent<Character>() : null; if (c == null) { fail(Wcp.INVALID_PARAMS, "no creature " + id); return true; } }
                    Control.Start(new SpeedAction(c, (float)MiniJson.Num(a, "seconds", 2), MiniJson.Str(a, "move", c == me ? "walk" : "none"), a.ContainsKey("yaw") ? (float?)MiniJson.Num(a, "yaw", 0) : null), ok, fail); return true;
                }
                case "sleep":
                {
                    string id = MiniJson.Str(a, "id", null); Vector3? aim = Aim(a, id, null);
                    if (aim == null) { fail(Wcp.INVALID_PARAMS, "sleep needs the bed's id or x, z"); return true; }
                    Control.Start(new SleepAction(aim.Value, id, (float)MiniJson.Num(a, "wait", 40)), ok, fail); return true;
                }
                case "deconstruct": case "repair":
                {
                    var nv = Query.FindById(MiniJson.Str(a, "id", ""));
                    if (nv == null || nv.GetComponent<Piece>() == null) { fail(Wcp.INVALID_PARAMS, "no build piece with id " + MiniJson.Str(a, "id", "")); return true; }
                    Control.Start(new HammerAction(nv, name == "repair"), ok, fail); return true;
                }
                case "repair_items":
                {
                    if (!InventoryGui.IsVisible() || me.GetCurrentCraftingStation() == null) { ok(Res(false, "open a crafting station first (use it)")); return true; }
                    var gui = InventoryGui.instance; int n = 0;
                    var have = AccessTools.Method(typeof(InventoryGui), "HaveRepairableItems"); var one = AccessTools.Method(typeof(InventoryGui), "RepairOneItem");
                    while (n < 50 && (bool)have.Invoke(gui, null)) { one.Invoke(gui, null); n++; }
                    ok(Res(n > 0, "nothing this station can repair", "repaired", n)); return true;
                }
                case "sail":
                {
                    var sh = me.GetControlledShip();
                    if (sh == null) { ok(Res(false, "not at a ship's helm (use the helm first)")); return true; }
                    Control.Start(new SailAction(sh, MiniJson.Str(a, "speed", null), a.ContainsKey("rudder") ? (float?)MiniJson.Num(a, "rudder", 0) : null, (float)MiniJson.Num(a, "seconds", 3)), ok, fail); return true;
                }
                case "dismount": Control.Start(new ButtonAction("Jump", 0f, 0.8f), ok, fail); return true;
                case "mount":
                {
                    var nv = Query.FindById(MiniJson.Str(a, "id", ""));
                    if (nv == null) { fail(Wcp.INVALID_PARAMS, "mount needs the id of a ship, a saddled tame or a chair"); return true; }
                    Component part = (Component)nv.GetComponentInChildren<ShipControlls>() ?? (Component)nv.GetComponentInChildren<Sadle>() ?? (Component)nv.GetComponentInChildren<Chair>();
                    if (part == null) { ok(Res(false, Utils.GetPrefabName(nv.gameObject) + " has no helm, saddle or seat")); return true; }
                    var col = part.GetComponentInChildren<Collider>();
                    var aim = col != null ? col.bounds.center + Vector3.up * col.bounds.extents.y * 0.8f : part.transform.position;
                    var ma = new MountAction(aim, MiniJson.Str(a, "id", ""), part, (float)MiniJson.Num(a, "reach", 2.0));
                    var sc = part as ShipControlls;
                    if (sc != null && sc.m_attachPoint != null && Vector3.Distance(me.transform.position, sc.m_attachPoint.position) > 1.0f) ma.Stand = sc.m_attachPoint.position;   // stand at the helm (walk on deck)
                    Control.Start(ma, ok, fail); return true;
                }
            }
            return false;
        }

        /// Where to stand to use a part that faces one way (a smelter's hatch): 1.3 m out from the object's centre through the part.
        static Vector3? StandFor(string id, Vector3 aim)
        {
            var nv = id != null ? Query.FindById(id) : null;
            if (nv == null || nv.GetComponent<Smelter>() == null) return null;
            var d = aim - nv.transform.position; d.y = 0;
            if (d.magnitude < 0.05f) return null;
            var s = aim + d.normalized * 1.3f; s.y = ZoneSystem.instance.GetGroundHeight(s);
            return s;
        }

        static Vector3? Aim(Dictionary<string, object> a, string id, ItemDrop.ItemData item)
        {
            if (id != null)
            {
                var nv = Query.FindById(id); if (nv == null) return null;
                var sm = nv.GetComponent<Smelter>();
                if (sm != null && item != null)             // aim at the right hatch: fuel or ore
                {
                    bool fuel = sm.m_fuelItem != null && item.m_dropPrefab != null && sm.m_fuelItem.gameObject.name == item.m_dropPrefab.name;
                    var sw = fuel ? sm.m_addWoodSwitch : sm.m_addOreSwitch;
                    if (sw != null) { var sc = sw.GetComponentInChildren<Collider>(); return sc != null ? sc.bounds.center : sw.transform.position; }
                }
                var col = nv.GetComponentInChildren<Collider>();
                return col != null ? col.bounds.center : nv.transform.position + Vector3.up * 0.5f;
            }
            if (!a.ContainsKey("x") || !a.ContainsKey("z")) return null;
            var v = new Vector3((float)MiniJson.Num(a, "x", 0), 0, (float)MiniJson.Num(a, "z", 0));
            v.y = a.ContainsKey("y") ? (float)MiniJson.Num(a, "y", 0) : ZoneSystem.instance.GetGroundHeight(v);
            return v;
        }

        public static string Res(bool ok, string why, params object[] extra)
        {
            var kv = new List<object> { "ok", ok, "status", ok ? "done" : "failed", "error", ok ? null : (object)Query.D("code", Wcp.ACTION_FAILED, "message", why),
                                        "ticks", 0, "wall_ms", 0, "effects", new ArrayList(), "events", new ArrayList() };
            kv.AddRange(extra);
            if (Player.m_localPlayer != null) { kv.Add("player"); kv.Add(IAction.Brief()); }
            return Json.Obj(kv.ToArray());
        }
    }

    // ==================================================================== actions
    /// Walk up to a target (if farther than reach), aim at it, then run `Act` once the hover check settled.
    public abstract class AimedAction : IAction
    {
        protected readonly Vector3 aim; protected readonly string wantId; protected readonly float reach;
        protected int f; MoveAction walk; protected bool walked; protected object hovered; public Vector3? Stand; protected bool lenient; Vector3? detour; bool detourDone;
        protected AimedAction(Vector3 aim, string id, float reach) { this.aim = aim; wantId = id; this.reach = reach; }
        /// null = keep going (after the aim settled); otherwise the result
        protected abstract string Act(int k);
        public override string Step()
        {
            Tick();
            if (f == 0)
            {
                var goal = Stand ?? aim; float rad = Stand.HasValue ? 0.5f : reach * 0.8f;
                if (Stand.HasValue && detour == null && !detourDone)
                {   // the straight line to the stand point crosses the object: go around it first (move_to has no path finding)
                    var p0 = P.transform.position; var seg = goal - p0; seg.y = 0; var toC = aim - p0; toC.y = 0;
                    float t = seg.sqrMagnitude > 0.01f ? Mathf.Clamp01(Vector3.Dot(toC, seg) / seg.sqrMagnitude) : 0;
                    var closest = p0 + seg * t; closest.y = aim.y;
                    if (t > 0.05f && t < 0.95f && Vector3.Distance(new Vector3(closest.x, 0, closest.z), new Vector3(aim.x, 0, aim.z)) < 1.8f)
                    {
                        var side = Vector3.Cross(Vector3.up, seg.normalized); if (Vector3.Dot(side, p0 - aim) < 0) side = -side;
                        detour = aim + side * 2.6f;
                    }
                    detourDone = true;
                }
                if (detour.HasValue)
                {
                    if (walk == null) { walk = new MoveAction(detour.Value.x, detour.Value.z, 0.8f, false, 15f); walk.Begin(); walked = true; }
                    var r0 = walk.Step(); if (r0 == null) return null;
                    VInput.ReleaseAll(); walk = null; detour = null;
                }
                var d = goal - P.transform.position; d.y = 0;
                if (d.magnitude > (Stand.HasValue ? 0.6f : reach))
                {
                    if (walk == null) { walk = new MoveAction(goal.x, goal.z, rad, false, 20f); walk.Begin(); walked = true; }
                    var r = walk.Step(); if (r == null) return null;
                    VInput.ReleaseAll(); if (r.Contains("\"ok\":false")) return Failed("could not walk up to the target", "walk", new Json.Raw(r));
                    walk = null;
                }
            }
            f++;
            if (f == 1 && P.InPlaceMode() && P.RightItem != null) P.UnequipItem(P.RightItem);
            VInput.AimTarget = aim; if (f == 1) LookAction.LookAt(P, aim);
            if (f < 4) return null;
            if (f == 4)
            {
                var h = P.GetHoverObject(); hovered = h != null ? (object)Query.HoverInfo(h) : null;
                if (lenient) return Act(0);
                if (h == null) return Failed("nothing to interact with under the cursor at the target (blocked or too far: interact range ~" + Query.R(P.m_maxInteractDistance) + " m)");
                if (wantId != null)
                {
                    var nv = h.GetComponentInParent<ZNetView>();
                    if (nv == null || nv.GetZDO() == null || nv.GetZDO().m_uid.ToString() != wantId)
                        return Failed("the cursor is on another object (" + Utils.GetPrefabName(nv != null ? nv.gameObject : h) + "): the target is occluded", "hovered", hovered);
                }
            }
            return Act(f - 4);
        }
        public override void Finish() { VInput.AimTarget = null; }
    }

    public class UseItemOnAction : AimedAction
    {
        readonly ItemDrop.ItemData item; int n0;
        public UseItemOnAction(Vector3 aim, string id, ItemDrop.ItemData item, float reach) : base(aim, id, reach) { this.item = item; }
        protected override string Act(int k)
        {
            var inv = P.GetInventory();
            if (k == 0) { n0 = inv.CountItems(item.m_shared.m_name); P.UseItem(inv, item, false); return null; }
            if (k < 12) return null;
            int n1 = inv.CountItems(item.m_shared.m_name);
            return Ok("hovered", hovered, "walked", walked, "item", item.m_dropPrefab != null ? item.m_dropPrefab.name : item.m_shared.m_name, "count_before", n0, "count_after", n1,
                      "consumed", n0 - n1, "note", "ok = the item was offered; see consumed / events (e.g. 'wrong item') for the outcome");
        }
    }

    /// Use a helm / saddle / seat: press E when the cursor is on that part, otherwise call the part's own Interact (the
    /// same routine the E press runs; the third-person camera often sees the creature's body or the hull instead).
    public class MountAction : AimedAction
    {
        readonly Component part; string via;
        public MountAction(Vector3 aim, string id, Component part, float reach) : base(aim, null, reach) { this.part = part; lenient = true; }
        protected override string Act(int k)
        {
            if (k == 0)
            {
                var h = P.GetHoverObject(); var hv = h != null ? h.GetComponentInParent<Hoverable>() : null;
                if (hv != null && (object)hv == (object)part) { via = "use"; VInput.Press("Use", 2); }
                else { via = "interact"; ((Interactable)part).Interact(P, false, false); }
                return null;
            }
            if (k < 20) return null;
            var ship = P.GetControlledShip(); var ap = P.GetAttachPoint();
            bool on = P.IsAttached() || P.IsRiding() || ship != null;
            return on ? Ok("via", via, "part", part.GetType().Name, "riding", P.IsRiding(), "at_helm", ship != null, "attached", P.IsAttached())
                      : Failed("did not get on (" + part.GetType().Name + "; see events: too far, not tamed, in use)", "via", via, "hovered", hovered);
        }
    }

    public class SleepAction : AimedAction
    {
        readonly float wait; int presses; int day0; bool slept, wasInBed; float t0 = -1;
        public SleepAction(Vector3 aim, string id, float wait) : base(aim, id, 1.5f) { this.wait = Mathf.Clamp(wait, 5, 120); }
        protected override string Act(int k)
        {
            if (k == 0) day0 = EnvMan.instance.GetDay();
            if (!P.InBed() && presses < 2 && (k == 0 || k == 30)) { presses++; VInput.Press("Use", 2); return null; }
            if (P.InBed()) wasInBed = true;
            if (P.IsSleeping()) slept = true;
            if (k < 45) return null;
            if (!wasInBed) return Failed("did not get into the bed (see events: needs a roof, a fire nearby, no enemies, dry, at night)", "hovered", hovered, "presses", presses);
            if (t0 < 0) t0 = T;
            bool morning = EnvMan.instance.GetDay() > day0 && !EnvMan.instance.IsTimeSkipping();
            if (!morning && T - t0 < wait) return null;
            return Ok("in_bed", P.InBed(), "slept", slept || morning, "day_before", day0, "day_after", EnvMan.instance.GetDay(),
                      "time_of_day", Query.TimeOfDay(EnvMan.instance.GetDayFraction()), "presses", presses);
        }
    }

    /// Hammer: remove (deconstruct) or repair a piece. Equips the build tool, aims at the piece, presses Remove / Attack.
    public class HammerAction : IAction
    {
        readonly ZNetView target; readonly bool repair; int f; Vector3 aim; object hp0; Dictionary<string, int> inv0; string pf; ItemDrop.ItemData tool;
        public HammerAction(ZNetView t, bool repair) { target = t; this.repair = repair; }
        Dictionary<string, int> Counts() { var d = new Dictionary<string, int>(); foreach (var it in P.GetInventory().GetAllItems()) { var k = it.m_dropPrefab != null ? it.m_dropPrefab.name : it.m_shared.m_name; d[k] = (d.ContainsKey(k) ? d[k] : 0) + it.m_stack; } return d; }
        public override string Step()
        {
            Tick(); f++;
            if (f == 1)
            {
                pf = Utils.GetPrefabName(target.gameObject); var col = target.GetComponentInChildren<Collider>();
                aim = col != null ? col.bounds.center : target.transform.position + Vector3.up * 0.5f;
                foreach (var it in P.GetInventory().GetAllItems())
                {
                    var bp = it.m_shared.m_buildPieces; if (bp == null) continue;
                    bool fits = repair ? bp.m_pieces.Exists(g => g != null && g.GetComponent<Piece>() != null && g.GetComponent<Piece>().m_repairPiece) : bp.m_canRemovePieces;
                    if (!fits) continue;
                    tool = it; if (it.m_shared.m_name == "$item_hammer") break;
                }
                if (tool == null) return Failed("needs a hammer (a build tool) in the inventory");
                if (Vector3.Distance(P.m_eye.position, aim) > P.m_maxPlaceDistance) return Failed("the piece is " + Query.R(Vector3.Distance(P.m_eye.position, aim)) + " m away (build range " + P.m_maxPlaceDistance + " m): move closer first");
                object m; hp0 = Query.Health(target, out m); inv0 = Counts();
                if (P.RightItem != tool) P.EquipItem(tool);
                return null;
            }
            VInput.AimTarget = aim;
            if (f == 3)
            {
                if (!P.InPlaceMode()) return Failed("could not enter build mode");
                if (repair)
                {
                    Piece rp = null; foreach (var g in tool.m_shared.m_buildPieces.m_pieces) { var pc = g != null ? g.GetComponent<Piece>() : null; if (pc != null && pc.m_repairPiece) { rp = pc; break; } }
                    if (rp == null || !P.SetSelectedPiece(rp)) return Failed("the build tool has no repair entry");
                }
                LookAction.LookAt(P, aim);
            }
            if (f < 8) return null;
            if (f == 8) { if (repair) VInput.Press("Attack", 2); else VInput.Press("Remove", 2); }
            if (f < 30 || (!repair && f < 75)) return null;      // removed pieces drop their materials, picked up after a moment
            bool gone = !target || !target.IsValid() || Query.FindById(target.GetZDO() != null ? target.GetZDO().m_uid.ToString() : "-") == null;
            var inv1 = Counts(); var refund = new OrderedDictionary();
            foreach (var kv in inv1) { int b = inv0.ContainsKey(kv.Key) ? inv0[kv.Key] : 0; if (kv.Value > b) refund[kv.Key] = kv.Value - b; }
            var drops = new OrderedDictionary();
            if (!repair) foreach (var nv in Query.Instances())
            {
                var idr = nv.GetComponent<ItemDrop>(); if (idr == null || Vector3.Distance(nv.transform.position, aim) > 4f) continue;
                var k = Utils.GetPrefabName(nv.gameObject); drops[k] = (drops.Contains(k) ? (int)drops[k] : 0) + idr.m_itemData.m_stack;
            }
            if (!repair) return gone ? Ok("removed", pf, "refund", refund, "drops_near", drops) : Failed("the piece is still there (see events: no-build zone, ward, cannot be removed)", "piece", pf);
            object m2; var hp1 = gone ? null : Query.Health(target, out m2);
            bool better = hp0 is float && hp1 is float && (float)hp1 > (float)hp0;
            return better ? Ok("piece", pf, "health_before", hp0, "health_after", hp1) : Failed("not repaired (see events: does not need repair, no workbench nearby)", "piece", pf, "health_before", hp0, "health_after", hp1);
        }
        public override void Finish() { VInput.AimTarget = null; }
    }

    public class SpeedAction : IAction
    {
        readonly Character c; readonly float dur; readonly string move; readonly float? yaw; Vector3 p0, last; float dist, vmax; int frames;
        public SpeedAction(Character c, float seconds, string move, float? yaw) { this.c = c; dur = Mathf.Clamp(seconds, 0.2f, 30f); this.move = move; this.yaw = yaw; }
        public override string Step()
        {
            Tick(); frames++;
            if (c == null) return Failed("the creature is gone");
            var pos = c.transform.position;
            if (frames == 1) { p0 = pos; last = pos; if (yaw.HasValue && c == P) LookAction.Look(P, yaw.Value, 0); }
            else
            {
                var d = pos - last; d.y = 0; dist += d.magnitude; if (Time.deltaTime > 0) vmax = Mathf.Max(vmax, d.magnitude / Time.deltaTime); last = pos;
            }
            if (c == P && move != "none")
            {
                VInput.Hold(move == "back" ? "Backward" : "Forward");
                if (move == "run") VInput.Hold("Run");
            }
            if (T < dur) return null;
            var disp = pos - p0; disp.y = 0;
            float eqm = 0; try { if (c is Player) eqm = (float)AccessTools.Method(c.GetType(), "GetEquipmentMovementModifier").Invoke(c, null); } catch { }
            return Ok("seconds", Query.R(T), "path_m", Query.R(dist), "avg_speed", Query.R(dist / Mathf.Max(T, 0.01f)), "displacement_speed", Query.R(disp.magnitude / Mathf.Max(T, 0.01f)),
                      "max_speed", Query.R(vmax), "start", Query.V(p0), "end", Query.V(pos), "move", move, "running", c.IsRunning(), "encumbered", c is Player && ((Player)c).IsEncumbered(),
                      "base", Query.D("walk", Query.R(c.m_walkSpeed), "speed", Query.R(c.m_speed), "run", Query.R(c.m_runSpeed), "swim", Query.R(c.m_swimSpeed)),
                      "equipment_movement_modifier", Query.R(eqm));
        }
    }

    public class SailAction : IAction
    {
        readonly Ship ship; readonly string want; readonly float? rudder; readonly float dur; int f, presses; Vector3 p0; float tSettled = -1;
        static readonly string[] Order = { "Back", "Stop", "Slow", "Half", "Full" };
        public SailAction(Ship s, string speed, float? rudder, float seconds) { ship = s; want = speed; this.rudder = rudder; dur = Mathf.Clamp(seconds, 0, 60); }
        int Cur() { return Array.IndexOf(Order, Query.F<object>(ship, "m_speed").ToString()); }
        public override string Step()
        {
            Tick(); f++;
            if (f == 1) p0 = ship.transform.position;
            if (P.GetControlledShip() != ship) return Failed("left the helm");
            int target = want == null ? Cur() : Array.FindIndex(Order, s => string.Equals(s, want, StringComparison.OrdinalIgnoreCase));
            if (target < 0) return Failed("speed: stop|back|slow|half|full");
            int cur = Cur();
            if (cur != target && presses < 12) { if (f % 8 == 0) { VInput.Press(target > cur ? "Forward" : "Backward", 2); presses++; } return null; }
            if (rudder.HasValue)
            {
                float rv = Query.F<float>(ship, "m_rudderValue");
                if (Mathf.Abs(rv - rudder.Value) > 0.08f) VInput.Hold(rudder.Value > rv ? "Right" : "Left");
            }
            if (tSettled < 0) tSettled = T;
            if (T - tSettled < dur) return null;
            var body = ship.GetComponent<Rigidbody>();
            return Ok("speed", Order[Mathf.Max(0, Cur())], "rudder", Query.R(Query.F<float>(ship, "m_rudderValue")), "velocity", body != null ? Query.R(body.linearVelocity.magnitude) : 0f,
                      "moved_m", Query.R(Vector3.Distance(p0, ship.transform.position)), "ship_pos", Query.V(ship.transform.position), "presses", presses);
        }
    }

    // ==================================================================== patches
    [HarmonyPatch(typeof(Player), "RemovePiece")]
    static class PatchRemoveAim
    {
        static void Prefix(ref Quaternion __state) { Aim.Prefix(ref __state); }
        static void Postfix(Quaternion __state) { Aim.Postfix(__state); }
    }

    [HarmonyPatch(typeof(Player), "UpdateWearNTearHover")]
    static class PatchWearHoverAim
    {
        static void Prefix(ref Quaternion __state) { Aim.Prefix(ref __state); }
        static void Postfix(Quaternion __state) { Aim.Postfix(__state); }
    }

    [HarmonyPatch(typeof(Chat), nameof(Chat.SetNpcText))]
    static class PatchNpcText
    {
        static void Prefix(GameObject talker, string topic, string text)
        {
            try
            {
                var nv = talker != null ? talker.GetComponentInParent<ZNetView>() : null;
                var t = new Extra.Talk { T = Time.time, Prefab = talker != null ? Utils.GetPrefabName(nv != null ? nv.gameObject : talker) : null,
                                         Id = nv != null && nv.GetZDO() != null ? nv.GetZDO().m_uid.ToString() : null, Pos = talker != null ? talker.transform.position : Vector3.zero,
                                         Topic = Query.Loc(topic), Text = Query.Loc(text) };
                lock (Extra.Talks) { Extra.Talks.Add(t); Extra.TalkTotal++; if (Extra.Talks.Count > 200) Extra.Talks.RemoveAt(0); }
            }
            catch { }
        }
    }
}
