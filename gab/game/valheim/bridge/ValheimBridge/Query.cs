// Reading game state: player status, inventory, equipment, skills, status effects, nearby objects / pieces / containers.
using System;
using System.Collections;
using System.Collections.Generic;
using System.Collections.Specialized;
using HarmonyLib;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace ValheimBridge
{
    public static class Query
    {
        public static OrderedDictionary D(params object[] kv)
        {
            var d = new OrderedDictionary();
            for (int i = 0; i + 1 < kv.Length; i += 2) d[(string)kv[i]] = kv[i + 1];
            return d;
        }

        public static T F<T>(object o, string field) { return Traverse.Create(o).Field(field).GetValue<T>(); }
        public static T SF<T>(Type t, string field) { return Traverse.Create(t).Field(field).GetValue<T>(); }
        public static object Call(object o, string method, params object[] args) { return AccessTools.Method(o.GetType(), method).Invoke(o, args); }

        public static float[] V(Vector3 v) { return new[] { R(v.x), R(v.y), R(v.z) }; }
        public static float R(float f) { return Mathf.Round(f * 100f) / 100f; }

        public static string Loc(string s)
        {
            if (string.IsNullOrEmpty(s)) return s;
            try { return Localization.instance != null ? Localization.instance.Localize(s) : s; } catch { return s; }
        }

        public static Player Me() { return Player.m_localPlayer; }
        public static Player MeOrThrow()
        {
            var p = Player.m_localPlayer;
            if (p == null) throw new WcpException(Wcp.NOT_READY, "not in a world (no local player); reset first");
            return p;
        }

        public static bool Paused() { return ZNet.instance != null && Player.m_localPlayer != null && Game.IsPaused(); }

        public static string Scene() { return SceneManager.GetActiveScene().name; }

        // ---------------------------------------------------------------- status
        public static string TimeOfDay(float frac) { int m = (int)(frac * 24f * 60f) % 1440; return (m / 60).ToString("00") + ":" + (m % 60).ToString("00"); }

        public static OrderedDictionary Status()
        {
            var p = Me();
            var d = D("scene", Scene(), "in_world", p != null, "session", Session.Phase);
            if (p == null) return d;
            var pos = p.transform.position;
            d["name"] = p.GetPlayerName();
            d["pos"] = V(pos);
            d["yaw"] = R(p.transform.rotation.eulerAngles.y);
            d["look_yaw"] = R(p.GetLookYaw().eulerAngles.y);
            d["look_pitch"] = R(F<float>(p, "m_lookPitch"));
            d["health"] = R(p.GetHealth()); d["max_health"] = R(p.GetMaxHealth());
            d["stamina"] = R(p.GetStamina()); d["max_stamina"] = R(p.GetMaxStamina());
            d["eitr"] = R(p.GetEitr()); d["max_eitr"] = R(p.GetMaxEitr());
            d["dead"] = p.IsDead(); d["teleporting"] = p.IsTeleporting();
            d["biome"] = Heightmap.FindBiome(pos).ToString();
            d["ground_height"] = ZoneSystem.instance != null ? R(ZoneSystem.instance.GetGroundHeight(pos)) : 0f;
            d["on_ground"] = p.IsOnGround(); d["swimming"] = p.IsSwimming(); d["crouching"] = p.IsCrouching();
            d["god_mode"] = p.InGodMode(); d["ghost_mode"] = p.InGhostMode(); d["no_cost"] = p.NoCostCheat();
            d["debug_fly"] = p.IsDebugFlying();
            d["in_place_mode"] = p.InPlaceMode();
            var sel = p.InPlaceMode() ? p.GetSelectedPiece() : null;
            d["selected_piece"] = sel != null ? Utils.GetPrefabName(sel.gameObject) : null;
            var hov = p.GetHoverObject();
            d["hovering"] = hov != null ? HoverInfo(hov) : null;
            var st = p.GetCurrentCraftingStation();
            d["crafting_station"] = st != null ? Utils.GetPrefabName(st.gameObject) : null;
            d["inventory_open"] = InventoryGui.IsVisible();
            d["weight"] = R(p.GetInventory().GetTotalWeight()); d["max_weight"] = R(p.GetMaxCarryWeight());
            var env = EnvMan.instance;
            if (env != null)
            {
                float fr = env.GetDayFraction();
                d["day"] = env.GetDay(); d["day_fraction"] = (float)Math.Round(fr, 4); d["time_of_day"] = TimeOfDay(fr);
                d["is_day"] = EnvMan.IsDay();
                var e = env.GetCurrentEnvironment(); d["environment"] = e != null ? e.m_name : null;
            }
            d["paused"] = Query.Paused();
            // 0.2: bed / seat / mount / helm
            d["attached"] = p.IsAttached(); d["in_bed"] = p.InBed(); d["sleeping"] = p.IsSleeping(); d["riding"] = p.IsRiding();
            var ap = p.GetAttachPoint(); var apn = ap != null ? ap.GetComponentInParent<ZNetView>() : null;
            d["attached_to"] = apn != null ? Utils.GetPrefabName(apn.gameObject) : null;
            var ship = p.GetControlledShip(); d["at_helm"] = ship != null ? Utils.GetPrefabName(ship.gameObject) : null;
            d["on_ship"] = p.IsAttachedToShip() || Ship.GetLocalShip() != null;
            if (env != null) d["time_skipping"] = env.IsTimeSkipping();
            return d;
        }

        public static OrderedDictionary HoverInfo(GameObject go)
        {
            var nv = go.GetComponentInParent<ZNetView>();
            var root = nv != null ? nv.gameObject : go;
            var h = go.GetComponentInParent<Hoverable>();
            string text = null;
            try { text = h != null ? Loc(h.GetHoverText()) : null; } catch { }
            return D("prefab", Utils.GetPrefabName(root), "id", nv != null && nv.GetZDO() != null ? nv.GetZDO().m_uid.ToString() : null,
                     "pos", V(root.transform.position), "hover_name", h != null ? Loc(h.GetHoverName()) : null, "hover_text", text);
        }

        // ---------------------------------------------------------------- items
        public static OrderedDictionary Item(ItemDrop.ItemData it)
        {
            if (it == null) return null;
            return D("prefab", it.m_dropPrefab != null ? it.m_dropPrefab.name : null, "name", it.m_shared.m_name, "label", Loc(it.m_shared.m_name),
                     "stack", it.m_stack, "max_stack", it.m_shared.m_maxStackSize, "quality", it.m_quality, "variant", it.m_variant,
                     "durability", R(it.m_durability), "max_durability", R(it.GetMaxDurability()), "use_durability", it.m_shared.m_useDurability,
                     "equipped", it.m_equipped, "grid", new[] { it.m_gridPos.x, it.m_gridPos.y }, "type", it.m_shared.m_itemType.ToString(),
                     "weight", R(it.GetWeight()), "crafter", string.IsNullOrEmpty(it.m_crafterName) ? null : it.m_crafterName,
                     "custom_data", it.m_customData != null && it.m_customData.Count > 0 ? Extra.Dump(it.m_customData, 1) : null);
        }

        public static OrderedDictionary InventoryOf(Inventory inv)
        {
            var items = new ArrayList(); var counts = new OrderedDictionary();
            foreach (var it in inv.GetAllItems())
            {
                items.Add(Item(it));
                string k = it.m_dropPrefab != null ? it.m_dropPrefab.name : it.m_shared.m_name;
                counts[k] = (counts.Contains(k) ? (int)counts[k] : 0) + it.m_stack;
            }
            return D("name", inv.GetName(), "width", inv.GetWidth(), "height", inv.GetHeight(), "slots_used", inv.NrOfItems(),
                     "empty_slots", inv.GetEmptySlots(), "items", items, "counts", counts);
        }

        public static OrderedDictionary Equipment(Player p)
        {
            string[] slots = { "right", "m_rightItem", "left", "m_leftItem", "chest", "m_chestItem", "legs", "m_legItem", "helmet", "m_helmetItem",
                               "shoulder", "m_shoulderItem", "utility", "m_utilityItem", "trinket", "m_trinketItem", "ammo", "m_ammoItem" };
            var d = new OrderedDictionary();
            for (int i = 0; i < slots.Length; i += 2)
            {
                ItemDrop.ItemData it = null;
                try { it = F<ItemDrop.ItemData>(p, slots[i + 1]); } catch { }
                d[slots[i]] = Item(it);
            }
            return d;
        }

        /// Find an item in an inventory by prefab name (e.g. "Hammer"), $token ("$item_hammer") or localized label ("Hammer").
        public static ItemDrop.ItemData FindItem(Inventory inv, string q)
        {
            if (string.IsNullOrEmpty(q)) return null;
            string ql = q.ToLowerInvariant();
            foreach (var it in inv.GetAllItems())
                if ((it.m_dropPrefab != null && it.m_dropPrefab.name.ToLowerInvariant() == ql) || it.m_shared.m_name.ToLowerInvariant() == ql) return it;
            foreach (var it in inv.GetAllItems())
                if ((Loc(it.m_shared.m_name) ?? "").ToLowerInvariant() == ql) return it;
            return null;
        }

        public static ArrayList Skills(Player p)
        {
            var l = new ArrayList();
            foreach (var s in p.GetSkills().GetSkillList())
                l.Add(D("skill", s.m_info.m_skill.ToString(), "level", R(s.m_level), "accumulator", R(s.m_accumulator)));
            return l;
        }

        public static ArrayList StatusEffects(Character c)
        {
            var l = new ArrayList();
            foreach (var se in c.GetSEMan().GetStatusEffects())
                l.Add(D("prefab", se.name.Replace("(Clone)", ""), "name", se.m_name, "label", Loc(se.m_name), "ttl", R(se.m_ttl),
                        "remaining", se.m_ttl > 0 ? R(se.GetRemaningTime()) : (object)null));
            return l;
        }

        public static ArrayList Foods(Player p)
        {
            var l = new ArrayList();
            foreach (var f in p.GetFoods())
                l.Add(D("prefab", f.m_item != null && f.m_item.m_dropPrefab != null ? f.m_item.m_dropPrefab.name : null, "name", f.m_name,
                        "time_left", R(f.m_time), "health", R(f.m_health), "stamina", R(f.m_stamina), "eitr", R(f.m_eitr)));
            return l;
        }

        // ---------------------------------------------------------------- world objects
        public static IEnumerable<ZNetView> Instances()
        {
            var zs = ZNetScene.instance;
            if (zs == null) yield break;
            var inst = F<Dictionary<ZDO, ZNetView>>(zs, "m_instances");
            foreach (var nv in new List<ZNetView>(inst.Values)) if (nv != null && nv.IsValid()) yield return nv;
        }

        public static ZNetView FindById(string id)
        {
            foreach (var nv in Instances()) if (nv.GetZDO().m_uid.ToString() == id) return nv;
            return null;
        }

        public static string Kind(GameObject go)
        {
            if (go.GetComponent<Player>()) return "player";
            if (go.GetComponent<Character>()) return "creature";
            if (go.GetComponent<ItemDrop>()) return "item";
            if (go.GetComponent<Container>()) return "container";
            if (go.GetComponent<CraftingStation>()) return "crafting_station";
            if (go.GetComponent<Piece>()) return "piece";
            if (go.GetComponent<Pickable>()) return "pickable";
            if (go.GetComponent<TreeBase>()) return "tree";
            if (go.GetComponent<TreeLog>()) return "log";
            if (go.GetComponent<MineRock>() || go.GetComponent<MineRock5>()) return "rock";
            if (go.GetComponent<Destructible>()) return "destructible";
            return "other";
        }

        public static object Health(ZNetView nv, out object max)
        {
            var go = nv.gameObject; max = null;
            var c = go.GetComponent<Character>();
            if (c != null) { max = R(c.GetMaxHealth()); return R(c.GetHealth()); }
            var w = go.GetComponent<WearNTear>();
            if (w != null) { max = R(w.m_health); return R(nv.GetZDO().GetFloat(ZDOVars.s_health, w.m_health)); }
            var de = go.GetComponent<Destructible>();
            if (de != null) { max = R(de.m_health); return R(nv.GetZDO().GetFloat(ZDOVars.s_health, de.m_health)); }
            var t = go.GetComponent<TreeBase>();
            if (t != null) { max = R(t.m_health); return R(nv.GetZDO().GetFloat(ZDOVars.s_health, t.m_health)); }
            var tl = go.GetComponent<TreeLog>();
            if (tl != null) { max = R(tl.m_health); return R(nv.GetZDO().GetFloat(ZDOVars.s_health, tl.m_health)); }
            float h = nv.GetZDO().GetFloat(ZDOVars.s_health, float.NaN);
            return float.IsNaN(h) ? null : (object)R(h);
        }

        public static OrderedDictionary Obj(ZNetView nv, Vector3 center, bool detail)
        {
            var go = nv.gameObject; var pos = go.transform.position;
            object max; var hp = Health(nv, out max);
            var d = D("id", nv.GetZDO().m_uid.ToString(), "prefab", Utils.GetPrefabName(go), "kind", Kind(go), "pos", V(pos),
                      "dist", R(Vector3.Distance(center, pos)), "health", hp, "max_health", max);
            var hv = go.GetComponent<Hoverable>();
            if (hv != null) { try { d["label"] = Loc(hv.GetHoverName()); } catch { } }
            var it = go.GetComponent<ItemDrop>();
            if (it != null) { d["stack"] = it.m_itemData.m_stack; d["quality"] = it.m_itemData.m_quality; }
            var ch = go.GetComponent<Character>();
            if (ch != null) { d["level"] = ch.GetLevel(); d["tamed"] = ch.IsTamed(); d["faction"] = ch.m_faction.ToString(); d["dead"] = ch.IsDead(); }
            var pc = go.GetComponent<Piece>();
            if (pc != null) { d["yaw"] = R(go.transform.rotation.eulerAngles.y); d["creator"] = pc.GetCreator(); d["piece_name"] = pc.m_name; }
            var pk = go.GetComponent<Pickable>();
            if (pk != null) d["picked"] = nv.GetZDO().GetBool(ZDOVars.s_picked, false);
            try { Extra.Describe(nv, d, detail); } catch (Exception e) { d["describe_error"] = e.Message; }
            if (detail)
            {
                var ct = go.GetComponent<Container>();
                if (ct != null && ct.GetInventory() != null) d["inventory"] = InventoryOf(ct.GetInventory());
                var cs = go.GetComponent<CraftingStation>();
                if (cs != null) d["station_level"] = cs.GetLevel();
                var comps = new ArrayList();
                foreach (var c in go.GetComponents<MonoBehaviour>()) if (c != null) comps.Add(c.GetType().Name);
                d["components"] = comps;
            }
            return d;
        }

        /// args: x,y,z (default: the player), r (default 20), prefab (exact or substring with "*"), kind, limit (200), detail
        public static OrderedDictionary Objects(Dictionary<string, object> a, Func<ZNetView, bool> filter)
        {
            Vector3 c = Center(a); float r = (float)MiniJson.Num(a, "r", 20);
            string prefab = MiniJson.Str(a, "prefab", null), kind = MiniJson.Str(a, "kind", null);
            bool effects = a.ContainsKey("effects") && a["effects"] is bool && (bool)a["effects"];   // sfx_/vfx_/fx_ objects are skipped unless asked for
            int limit = (int)MiniJson.Num(a, "limit", 200); bool detail = a.ContainsKey("detail") && a["detail"] is bool && (bool)a["detail"];
            var hits = new List<KeyValuePair<float, ZNetView>>();
            foreach (var nv in Instances())
            {
                float dist = Vector3.Distance(nv.transform.position, c);
                if (dist > r) continue;
                string pn = Utils.GetPrefabName(nv.gameObject);
                if (!effects && prefab == null && (pn.StartsWith("sfx_") || pn.StartsWith("vfx_") || pn.StartsWith("fx_"))) continue;
                if (prefab != null && !Match(pn, prefab)) continue;
                if (kind != null && Kind(nv.gameObject) != kind) continue;
                if (filter != null && !filter(nv)) continue;
                hits.Add(new KeyValuePair<float, ZNetView>(dist, nv));
            }
            hits.Sort((x, y) => x.Key.CompareTo(y.Key));
            var l = new ArrayList();
            for (int i = 0; i < hits.Count && i < limit; i++) l.Add(Obj(hits[i].Value, c, detail));
            return D("center", V(c), "r", r, "count", hits.Count, "objects", l);
        }

        public static bool Match(string name, string pat)
        {
            if (pat.Contains("*"))
            {
                string p = pat.Replace("*", "").ToLowerInvariant();
                return name.ToLowerInvariant().Contains(p);
            }
            return string.Equals(name, pat, StringComparison.OrdinalIgnoreCase);
        }

        public static Vector3 Center(Dictionary<string, object> a)
        {
            var p = Me();
            Vector3 c = p != null ? p.transform.position : Vector3.zero;
            if (a.ContainsKey("x")) c.x = (float)MiniJson.Num(a, "x", 0);
            if (a.ContainsKey("z")) c.z = (float)MiniJson.Num(a, "z", 0);
            if (a.ContainsKey("y")) c.y = (float)MiniJson.Num(a, "y", 0);
            else if (a.ContainsKey("x") && ZoneSystem.instance != null) c.y = ZoneSystem.instance.GetGroundHeight(c);
            return c;
        }

        // ---------------------------------------------------------------- recipes / pieces
        public static Recipe FindRecipe(string q)
        {
            string ql = q.ToLowerInvariant();
            foreach (var r in ObjectDB.instance.m_recipes)
                if (r != null && r.m_item != null && (r.m_item.name.ToLowerInvariant() == ql || r.name.ToLowerInvariant() == ql || r.m_item.m_itemData.m_shared.m_name.ToLowerInvariant() == ql)) return r;
            return null;
        }

        public static OrderedDictionary RecipeInfo(Recipe r, Player p)
        {
            var req = new ArrayList();
            foreach (var q in r.m_resources) if (q.m_resItem != null) req.Add(D("prefab", q.m_resItem.name, "amount", q.m_amount, "per_level", q.m_amountPerLevel));
            return D("recipe", r.name, "item", r.m_item.name, "amount", r.m_amount, "enabled", r.m_enabled,
                     "station", r.m_craftingStation != null ? Utils.GetPrefabName(r.m_craftingStation.gameObject) : null, "min_station_level", r.m_minStationLevel,
                     "known", p != null && p.IsRecipeKnown(r.m_item.m_itemData.m_shared.m_name),
                     "can_craft_here", p != null && p.HaveRequirements(r, false, 1), "resources", req);
        }

        public static OrderedDictionary PieceInfo(Piece pc, Player p)
        {
            var req = new ArrayList();
            foreach (var q in pc.m_resources) if (q.m_resItem != null) req.Add(D("prefab", q.m_resItem.name, "amount", q.m_amount));
            return D("prefab", Utils.GetPrefabName(pc.gameObject), "name", pc.m_name, "label", Loc(pc.m_name), "category", pc.m_category.ToString(),
                     "station", pc.m_craftingStation != null ? Utils.GetPrefabName(pc.m_craftingStation.gameObject) : null,
                     "known", p != null && p.IsRecipeKnown(pc.m_name), "have_resources", p != null && p.HaveRequirements(pc, Player.RequirementMode.CanBuild),
                     "in_build_menu", p != null && p.InPlaceMode() && p.IsPieceAvailable(pc), "resources", req);
        }
    }
}
