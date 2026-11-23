// WCP v0.1 method dispatch for Valheim.
// Both channels: hello, health. Agent: get_state, get_frame (unsupported -> x11), list_actions, act.
// Harness only: pause, resume, advance, reset, leave, eval_query (command | status | inventory | equipment | skills |
//   status_effects | foods | objects | pieces | containers | object | recipes | build_pieces | prefabs | hover | messages).
using System;
using System.Collections;
using System.Collections.Generic;
using HarmonyLib;
using UnityEngine;

namespace ValheimBridge
{
    public static class Commands
    {
        static readonly HashSet<string> HarnessOnly = new HashSet<string> { "pause", "resume", "advance", "reset", "leave", "save", "save_template", "eval_query" };
        static readonly HashSet<string> Known = new HashSet<string> { "hello", "health", "get_state", "get_frame", "list_actions", "act",
                                                                       "pause", "resume", "advance", "reset", "leave", "save", "save_template", "eval_query" };

        public static void Dispatch(Plugin.Request req, Action<string> ok, Action<int, string> fail)
        {
            var m = req.Method; var p = req.Params;
            if (!Known.Contains(m)) { fail(Wcp.METHOD_NOT_FOUND, "unknown method " + m); return; }
            if (HarnessOnly.Contains(m) && !req.Harness) { fail(Wcp.FORBIDDEN, m + " is not allowed on the agent channel"); return; }
            switch (m)
            {
                case "hello": ok(Hello()); return;
                case "health": ok(Health()); return;
                case "get_state":
                    ok(Json.Obj("tick", Player.m_localPlayer != null && ZNet.instance != null ? (object)Math.Round(ZNet.instance.GetTimeSeconds(), 2) : 0,
                                "state", AgentState(), "ui", Query.D("scene", Query.Scene(), "inventory_open", InventoryGui.IsVisible()), "events", new ArrayList()));
                    return;
                case "get_frame": fail(Wcp.METHOD_NOT_FOUND, "no internal frame source; capture the X display"); return;
                case "list_actions": ok(Json.Obj("actions", ActionList())); return;
                case "act": Act(p, ok, fail); return;
                case "pause": Query.MeOrThrow(); Control.SetPaused(true); ok(Json.Obj("paused", Query.Paused())); return;
                case "resume": Control.SetPaused(false); ok(Json.Obj("paused", Query.Paused())); return;
                case "advance": Query.MeOrThrow(); Control.Start(new WaitAction((float)MiniJson.Num(p, "seconds", 1.0)), ok, fail); return;
                case "reset": Session.Start(MiniJson.Dict(p, "scenario"), ok, fail); return;
                case "leave": Session.Leave(); ok(Json.Obj("status", "leaving")); return;
                case "save":
                    Query.MeOrThrow(); Game.instance.SavePlayerProfile(true); ZNet.instance.Save(true, false, false);
                    ok(Json.Obj("saved", true)); return;
                case "save_template": ok(Templates.Save(MiniJson.Str(p, "name", "default"), p.ContainsKey("keep_position") && p["keep_position"] is bool && (bool)p["keep_position"])); return;
                case "eval_query": ok(EvalQuery(MiniJson.Str(p, "query", ""), MiniJson.Dict(p, "args"))); return;
            }
        }

        static string Hello()
        {
            string ver = null; try { ver = Version.GetVersionString(); } catch { }
            return Json.Obj("protocol", Wcp.Protocol, "game", "valheim", "game_build", ver, "bridge", "ValheimBridge", "bridge_version", Plugin.Version,
                            "capabilities", new ArrayList { "pause", "advance", "eval_query", "reset" }, "extra_queries", Extra.QueryNames,
                            "time_model", Query.D("pausable", true, "tick_unit", "game_seconds", "ticks_per_second", 1),
                            "frame_sources", new ArrayList { "x11" });
        }

        static string Health()
        {
            return Json.Obj("status", Session.Status(), "session", Session.Phase, "error", Session.Error, "scene", Query.Scene(),
                            "busy", Control.Busy, "paused", Query.Paused(), "last_load_seconds", Query.R(Session.LastLoadSeconds),
                            "fps", Time.smoothDeltaTime > 0 ? 1f / Time.smoothDeltaTime : 0f);
        }

        static object AgentState()
        {
            var p = Query.Me();
            if (p == null) return Query.D("in_world", false, "scene", Query.Scene());
            var s = Query.Status();
            s["inventory"] = Query.InventoryOf(p.GetInventory())["items"];
            s["equipment"] = Query.Equipment(p);
            s["status_effects"] = Query.StatusEffects(p);
            s["foods"] = Query.Foods(p);
            s["nearby"] = Query.Objects(new Dictionary<string, object> { { "r", 12.0 }, { "limit", 25 } }, nv => nv.GetComponent<Player>() == null)["objects"];
            return s;
        }

        // ---------------------------------------------------------------- actions
        static Hashtable A(string name, string doc, Hashtable props, bool available, string reason)
        {
            var h = new Hashtable { { "name", name }, { "doc", doc }, { "args", new Hashtable { { "type", "object" }, { "properties", props } } }, { "available", available } };
            if (!available) h["reason"] = reason;
            return h;
        }
        static Hashtable T(string type) { return new Hashtable { { "type", type } }; }
        static Hashtable T(string type, object def) { return new Hashtable { { "type", type }, { "default", def } }; }

        static ArrayList ActionList()
        {
            var p = Query.Me(); bool live = p != null && !p.IsDead(); string why = p == null ? "not in a world" : "the player is dead";
            var xyz = new Hashtable { { "x", T("number") }, { "y", T("number") }, { "z", T("number") } };
            var list = new ArrayList {
                A("move_to", "walk (or run) to (x, z)", new Hashtable { { "x", T("number") }, { "z", T("number") }, { "radius", T("number", 1.0) }, { "run", T("boolean", false) }, { "timeout", T("number", 30) } }, live, why),
                A("look_at", "turn the view to a point (x, y, z) or to yaw/pitch degrees", new Hashtable { { "x", T("number") }, { "y", T("number") }, { "z", T("number") }, { "yaw", T("number") }, { "pitch", T("number") } }, live, why),
                A("use", "interact (E) with the hovered object, or aim at object id / point first", new Hashtable { { "id", T("string") }, { "x", T("number") }, { "y", T("number") }, { "z", T("number") }, { "hold", T("boolean", false) } }, live, why),
                A("attack", "primary attack (toward an optional point)", xyz, live, why),
                A("secondary_attack", "secondary attack", xyz, live, why),
                A("block", "hold block", new Hashtable { { "seconds", T("number", 1.0) } }, live, why),
                A("jump", "jump", new Hashtable(), live, why),
                A("crouch", "toggle crouch (sneak)", new Hashtable(), live, why),
                A("dodge", "dodge roll (while moving)", new Hashtable(), live, why),
                A("hold_key", "hold any game button (Forward, Backward, Left, Right, Run, Use, Hide, ...) for seconds", new Hashtable { { "button", T("string") }, { "seconds", T("number", 0.5) } }, live, why),
                A("equip", "equip an inventory item (prefab, $token or name)", new Hashtable { { "item", T("string") } }, live, why),
                A("unequip", "unequip an item", new Hashtable { { "item", T("string") } }, live, why),
                A("use_item", "use / consume an inventory item (eat food, drink a potion, toggle equip)", new Hashtable { { "item", T("string") } }, live, why),
                A("hotbar", "use hotbar slot 1-8", new Hashtable { { "slot", T("integer") } }, live, why),
                A("hide_weapons", "put away / take out hand items", new Hashtable(), live, why),
                A("drop_item", "drop an inventory item stack (or amount)", new Hashtable { { "item", T("string") }, { "amount", T("integer") } }, live, why),
                A("place", "place a build piece (prefab) at (x, y, z) with the hammer, rotated by yaw degrees", new Hashtable { { "piece", T("string") }, { "x", T("number") }, { "y", T("number") }, { "z", T("number") }, { "yaw", T("number", 0) } }, live, why),
                A("craft", "craft a recipe (item prefab) at the open crafting station (open it with use first) or by hand", new Hashtable { { "recipe", T("string") }, { "amount", T("integer", 1) } }, live, why),
                A("open_inventory", "open the inventory screen", new Hashtable(), live, why),
                A("close_inventory", "close the inventory / container / crafting screen", new Hashtable(), live, why),
                A("take_all", "take all items from the open container", new Hashtable(), live, why),
                A("wait", "let the game run for seconds", new Hashtable { { "seconds", T("number", 1.0) } }, p != null, "not in a world"),
            };
            Extra.AddActions(list, live, why);
            return list;
        }

        static Vector3? Point(Dictionary<string, object> a)
        {
            if (!a.ContainsKey("x") || !a.ContainsKey("z")) return null;
            var v = new Vector3((float)MiniJson.Num(a, "x", 0), 0, (float)MiniJson.Num(a, "z", 0));
            v.y = a.ContainsKey("y") ? (float)MiniJson.Num(a, "y", 0) : (ZoneSystem.instance != null ? ZoneSystem.instance.GetGroundHeight(v) : 0f);
            return v;
        }

        static string Done(bool ok, string why, params object[] extra)
        {
            var kv = new List<object> { "ok", ok, "status", ok ? "done" : "failed", "error", ok ? null : (object)Query.D("code", Wcp.ACTION_FAILED, "message", why),
                                        "ticks", 0, "wall_ms", 0, "effects", new ArrayList(), "events", new ArrayList() };
            kv.AddRange(extra);
            if (Player.m_localPlayer != null) { kv.Add("player"); kv.Add(IAction.Brief()); }
            return Json.Obj(kv.ToArray());
        }

        static void Act(Dictionary<string, object> p, Action<string> ok, Action<int, string> fail)
        {
            var name = MiniJson.Str(p, "name", null); var a = MiniJson.Dict(p, "args");
            if (name == null) { fail(Wcp.INVALID_PARAMS, "act needs a 'name'"); return; }
            var me = Query.Me();
            if (me == null) { fail(Wcp.UNAVAILABLE, "not in a world"); return; }
            if (me.IsDead() && name != "wait") { ok(Done(false, "the player is dead")); return; }
            var inv = me.GetInventory();
            switch (name)
            {
                case "move_to": Control.Start(new MoveAction((float)MiniJson.Num(a, "x", 0), (float)MiniJson.Num(a, "z", 0), (float)MiniJson.Num(a, "radius", 1.0), Bool(a, "run"), (float)MiniJson.Num(a, "timeout", 30)), ok, fail); return;
                case "look_at":
                    if (a.ContainsKey("yaw")) Control.Start(new LookAction((float)MiniJson.Num(a, "yaw", 0), (float)MiniJson.Num(a, "pitch", 0)), ok, fail);
                    else { var t = Point(a); if (t == null) { fail(Wcp.INVALID_PARAMS, "look_at needs x, z (y optional) or yaw/pitch"); return; } Control.Start(new LookAction(t.Value), ok, fail); }
                    return;
                case "use":
                {
                    string id = MiniJson.Str(a, "id", null); Vector3? aim = Point(a);
                    if (id != null)
                    {
                        var nv = Query.FindById(id);
                        if (nv == null) { ok(Done(false, "no object with id " + id + " nearby (not loaded?)")); return; }
                        var col = nv.GetComponentInChildren<Collider>();
                        aim = col != null ? col.bounds.center : nv.transform.position + Vector3.up * 0.5f;
                    }
                    Control.Start(new UseAction(aim, id, Bool(a, "hold"), !(a.ContainsKey("approach") && a["approach"] is bool && !(bool)a["approach"]), (float)MiniJson.Num(a, "reach", 1.5)), ok, fail); return;
                }
                case "attack": Control.Start(new AttackAction(false, Point(a)), ok, fail); return;
                case "secondary_attack": Control.Start(new AttackAction(true, Point(a)), ok, fail); return;
                case "block": Control.Start(new ButtonAction("Block", (float)MiniJson.Num(a, "seconds", 1.0), 0.1f), ok, fail); return;
                case "jump": Control.Start(new ButtonAction("Jump", 0f, 0.8f), ok, fail); return;
                case "crouch": Control.Start(new ButtonAction("Crouch", 0f, 0.3f), ok, fail); return;
                case "dodge": Control.Start(new ButtonAction("AltDodge", 0f, 0.8f), ok, fail); return;
                case "hold_key": Control.Start(new ButtonAction(MiniJson.Str(a, "button", "Forward"), (float)MiniJson.Num(a, "seconds", 0.5), 0.1f), ok, fail); return;
                case "hide_weapons": Control.Start(new ButtonAction("Hide", 0f, 0.5f), ok, fail); return;
                case "wait": Control.Start(new WaitAction((float)MiniJson.Num(a, "seconds", 1.0)), ok, fail); return;
                case "place":
                {
                    var t = Point(a); if (t == null) { fail(Wcp.INVALID_PARAMS, "place needs piece, x, z (y optional)"); return; }
                    Control.Start(new PlaceAction(MiniJson.Str(a, "piece", ""), t.Value, (float)MiniJson.Num(a, "yaw", 0)), ok, fail); return;
                }
                case "equip": case "unequip": case "use_item":
                {
                    var it = Query.FindItem(inv, MiniJson.Str(a, "item", ""));
                    if (it == null) { ok(Done(false, "no item '" + MiniJson.Str(a, "item", "") + "' in the inventory")); return; }
                    var before = Query.Item(it); int n0 = inv.CountItems(it.m_shared.m_name); int msg0 = PatchShowMessage.Total;
                    if (name == "equip") { if (!me.IsItemEquiped(it)) me.EquipItem(it); }
                    else if (name == "unequip") { if (me.IsItemEquiped(it)) me.UnequipItem(it); }
                    else me.UseItem(inv, it, true);
                    bool eq = me.IsItemEquiped(it); int n1 = inv.CountItems(it.m_shared.m_name);
                    bool success = name == "equip" ? eq : name == "unequip" ? !eq : true;
                    ok(Done(success, name + " had no effect (" + string.Join("; ", PatchShowMessage.Since(msg0).ToArray()) + ")", "item", before, "equipped", eq,
                            "count_before", n0, "count_after", n1, "messages", PatchShowMessage.Since(msg0)));
                    return;
                }
                case "hotbar":
                {
                    int slot = MiniJson.Int(a, "slot"); var it = inv.GetItemAt(slot - 1, 0);
                    if (it == null) { ok(Done(false, "hotbar slot " + slot + " is empty")); return; }
                    me.UseHotbarItem(slot);
                    ok(Done(true, null, "item", Query.Item(it), "equipped", me.IsItemEquiped(it))); return;
                }
                case "drop_item":
                {
                    var it = Query.FindItem(inv, MiniJson.Str(a, "item", ""));
                    if (it == null) { ok(Done(false, "no item '" + MiniJson.Str(a, "item", "") + "' in the inventory")); return; }
                    int amount = a.ContainsKey("amount") ? MiniJson.Int(a, "amount") : it.m_stack;
                    bool r = me.DropItem(inv, it, amount);
                    ok(Done(r, "could not drop the item", "item", Query.Item(it), "amount", amount)); return;
                }
                case "craft": ok(Craft(me, MiniJson.Str(a, "recipe", ""), a.ContainsKey("amount") ? MiniJson.Int(a, "amount") : 1)); return;
                case "open_inventory": if (!InventoryGui.IsVisible()) InventoryGui.instance.Show(null); Control.Start(new WaitAction(0.3f), ok, fail); return;
                case "close_inventory": if (InventoryGui.IsVisible()) InventoryGui.instance.Hide(); Control.Start(new WaitAction(0.3f), ok, fail); return;
                case "take_all":
                {
                    var c = Query.F<Container>(InventoryGui.instance, "m_currentContainer");
                    if (c == null || !InventoryGui.IsVisible()) { ok(Done(false, "no container is open (use it first)")); return; }
                    int n0 = inv.NrOfItems(); AccessTools.Method(typeof(InventoryGui), "OnTakeAll").Invoke(InventoryGui.instance, null);
                    ok(Done(true, null, "slots_before", n0, "slots_after", inv.NrOfItems())); return;
                }
                default:
                    if (Extra.Act(name, a, me, ok, fail)) return;
                    fail(Wcp.INVALID_PARAMS, "unknown action " + name); return;
            }
        }

        static bool Bool(Dictionary<string, object> a, string k) { return a.ContainsKey(k) && a[k] is bool && (bool)a[k]; }

        /// Craft through the crafting screen's own routine (InventoryGui.DoCrafting): station, recipe knowledge, resources,
        /// inventory space and skill bonus are the game's checks.
        static string Craft(Player me, string q, int amount)
        {
            var r = Query.FindRecipe(q);
            if (r == null) return Done(false, "no recipe for '" + q + "'");
            var info = Query.RecipeInfo(r, me);
            if (!me.IsRecipeKnown(r.m_item.m_itemData.m_shared.m_name) && !me.NoCostCheat()) return Done(false, "recipe " + r.m_item.name + " is not known yet", "recipe", info);
            if (!me.RequiredCraftingStation(r, 1, true))
                return Done(false, "needs crafting station " + info["station"] + " level " + r.m_minStationLevel + " (open it with use; current: " + (me.GetCurrentCraftingStation() != null ? Utils.GetPrefabName(me.GetCurrentCraftingStation().gameObject) : "none") + ")", "recipe", info);
            var gui = InventoryGui.instance;
            if (!InventoryGui.IsVisible()) gui.Show(null);
            var inv = me.GetInventory(); int before = inv.CountItems(r.m_item.m_itemData.m_shared.m_name); int crafted = 0; string why = null;
            for (int i = 0; i < Math.Max(1, amount); i++)
            {
                if (!me.HaveRequirements(r, false, 1) && !me.NoCostCheat()) { why = "missing resources"; break; }
                var t = Traverse.Create(gui);
                t.Field("m_craftRecipe").SetValue(r); t.Field("m_craftUpgradeItem").SetValue(null); t.Field("m_craftVariant").SetValue(0);
                t.Field("m_multiCrafting").SetValue(false);
                int n0 = inv.CountItems(r.m_item.m_itemData.m_shared.m_name);
                AccessTools.Method(typeof(InventoryGui), "DoCrafting").Invoke(gui, new object[] { me });
                t.Field("m_craftRecipe").SetValue(null);
                if (inv.CountItems(r.m_item.m_itemData.m_shared.m_name) <= n0) { why = "crafting did not add the item (inventory full?)"; break; }
                crafted++;
            }
            int after = inv.CountItems(r.m_item.m_itemData.m_shared.m_name);
            return Done(crafted > 0, why ?? "nothing crafted", "recipe", info, "crafted", crafted, "count_before", before, "count_after", after);
        }

        // ---------------------------------------------------------------- probes
        static string EvalQuery(string q, Dictionary<string, object> a)
        {
            switch (q)
            {
                case "command":
                {
                    string c = MiniJson.Str(a, "cmd", "");
                    if (Console.instance == null) throw new WcpException(Wcp.NOT_READY, "no console yet");
                    if (!Terminal.m_cheat && !(a.ContainsKey("cheats") && a["cheats"] is bool && !(bool)a["cheats"])) Session.EnableCheats();
                    var cap = new List<string>(); PatchAddString.Capture = cap; int msg0 = PatchShowMessage.Total;
                    try { Console.instance.TryRunCommand(c, false, true); }
                    finally { PatchAddString.Capture = null; }
                    bool failed = cap.Exists(s => s.Contains("is not a recognized command") || s.Contains("is not valid in the current context") || s.Contains("Error executing command"));
                    var outp = new ArrayList(); foreach (var s in cap) outp.Add(StripTags(s));
                    return Json.Obj("success", !failed, "messages", outp, "hud", PatchShowMessage.Since(msg0), "cheats", Terminal.m_cheat);
                }
                case "status": return Json.Val(Query.Status());
                case "inventory": return Json.Val(Query.InventoryOf(Query.MeOrThrow().GetInventory()));
                case "equipment": return Json.Val(Query.Equipment(Query.MeOrThrow()));
                case "skills": return Json.Obj("skills", Query.Skills(Query.MeOrThrow()));
                case "status_effects": return Json.Obj("status_effects", Query.StatusEffects(Query.MeOrThrow()));
                case "foods": return Json.Obj("foods", Query.Foods(Query.MeOrThrow()));
                case "objects": return Json.Val(Query.Objects(a, null));
                case "pieces": return Json.Val(Query.Objects(a, nv => nv.GetComponent<Piece>() != null && nv.GetComponent<Player>() == null));
                case "containers":
                    a["detail"] = true;
                    return Json.Val(Query.Objects(a, nv => nv.GetComponent<Container>() != null));
                case "object":
                {
                    var nv = Query.FindById(MiniJson.Str(a, "id", ""));
                    if (nv == null) return Json.Obj("object", null);
                    return Json.Obj("object", Query.Obj(nv, Query.Me() != null ? Query.Me().transform.position : Vector3.zero, true));
                }
                case "hover":
                {
                    var p = Query.MeOrThrow(); var h = p.GetHoverObject(); var hc = p.GetHoverCreature();
                    return Json.Obj("hover", h != null ? Query.HoverInfo(h) : null, "creature", hc != null ? Utils.GetPrefabName(hc.gameObject) : null);
                }
                case "recipes":
                {
                    var p = Query.Me(); string f = MiniJson.Str(a, "filter", null); bool knownOnly = Bool(a, "known");
                    var l = new ArrayList();
                    foreach (var r in ObjectDB.instance.m_recipes)
                    {
                        if (r == null || r.m_item == null) continue;
                        if (f != null && !Query.Match(r.m_item.name, f.Contains("*") ? f : "*" + f + "*")) continue;
                        if (knownOnly && (p == null || !p.IsRecipeKnown(r.m_item.m_itemData.m_shared.m_name))) continue;
                        l.Add(Query.RecipeInfo(r, p));
                    }
                    return Json.Obj("recipes", l);
                }
                case "build_pieces":
                {
                    var p = Query.MeOrThrow(); var l = new ArrayList(); string tool = MiniJson.Str(a, "tool", "Hammer");
                    var tp = ObjectDB.instance.GetItemPrefab(tool);
                    var table = tp != null ? tp.GetComponent<ItemDrop>().m_itemData.m_shared.m_buildPieces : null;
                    if (table == null) throw new WcpException(Wcp.INVALID_PARAMS, "no build tool " + tool);
                    foreach (var go in table.m_pieces) { var pc = go.GetComponent<Piece>(); if (pc != null) l.Add(Query.PieceInfo(pc, p)); }
                    return Json.Obj("tool", tool, "pieces", l);
                }
                case "prefabs":
                {
                    string f = MiniJson.Str(a, "filter", "").ToLowerInvariant(); var l = new ArrayList();
                    foreach (var go in ZNetScene.instance.m_prefabs) if (go != null && go.name.ToLowerInvariant().Contains(f)) l.Add(go.name);
                    return Json.Obj("count", l.Count, "prefabs", l);
                }
                // ---- fixtures (direct state edits for test setup; prefer them over console commands for exact positions/amounts)
                case "give":
                {
                    var me = Query.MeOrThrow(); string item = MiniJson.Str(a, "item", ""); int n = (int)MiniJson.Num(a, "amount", 1), qual = (int)MiniJson.Num(a, "quality", 1);
                    var pf = ObjectDB.instance.GetItemPrefab(item);
                    if (pf == null || pf.GetComponent<ItemDrop>() == null) return Json.Obj("success", false, "error", "no item prefab '" + item + "'");
                    int max = pf.GetComponent<ItemDrop>().m_itemData.m_shared.m_maxStackSize, given = 0;
                    while (given < n)
                    {
                        int k = Math.Min(n - given, Math.Max(1, max));
                        if (me.GetInventory().AddItem(item, k, qual, 0, 0L, "", false) == null) break;
                        given += k;
                    }
                    return Json.Obj("success", given == n, "given", given, "counts", Query.InventoryOf(me.GetInventory())["counts"]);
                }
                case "clear_inventory":
                {
                    var me = Query.MeOrThrow(); me.UnequipAllItems(); me.GetInventory().RemoveAll();
                    return Json.Obj("success", true);
                }
                case "teleport":
                {
                    var me = Query.MeOrThrow(); var t = Point(a) ?? me.transform.position;
                    if (!a.ContainsKey("y")) t.y += 0.2f;
                    float yaw = (float)MiniJson.Num(a, "yaw", me.transform.rotation.eulerAngles.y);
                    bool far = Vector3.Distance(t, me.transform.position) > 60f; bool accepted = true;
                    if (far) accepted = me.TeleportTo(t, Quaternion.Euler(0, yaw, 0), true);   // refused while teleporting / < 2 s after the last one
                    else { me.transform.position = t; me.transform.rotation = Quaternion.Euler(0, yaw, 0); var body = me.GetComponent<Rigidbody>(); if (body) { body.position = t; body.linearVelocity = Vector3.zero; } LookAction.Look(me, yaw, 0);
                           Traverse.Create(me).Field("m_maxAirAltitude").SetValue(t.y); }   // no fall damage from the old height
                    return Json.Obj("success", accepted, "pos", Query.V(t), "distant", far, "accepted", accepted,
                                    "note", far ? (accepted ? "distant teleport: wait until status.teleporting is false" : "refused: a teleport is running or ended < 2 s ago; retry") : null);
                }
                case "spawn":
                {
                    string pf = MiniJson.Str(a, "prefab", ""); var go = ZNetScene.instance.GetPrefab(pf);
                    if (go == null) return Json.Obj("success", false, "error", "no prefab '" + pf + "'");
                    var t = Point(a) ?? (Query.MeOrThrow().transform.position + Query.MeOrThrow().transform.forward * 2f);
                    int count = (int)MiniJson.Num(a, "count", 1), level = (int)MiniJson.Num(a, "level", 1);
                    var ids = new ArrayList();
                    for (int i = 0; i < count; i++)
                    {
                        var inst = UnityEngine.Object.Instantiate(go, t + new Vector3(0.4f * i, 0, 0), Quaternion.Euler(0, (float)MiniJson.Num(a, "yaw", 0), 0));
                        var ch = inst.GetComponent<Character>(); if (ch != null && level > 1) ch.SetLevel(level);
                        var idr = inst.GetComponent<ItemDrop>(); if (idr != null && a.ContainsKey("stack")) idr.SetStack((int)MiniJson.Num(a, "stack", 1));
                        var nv = inst.GetComponent<ZNetView>(); ids.Add(nv != null && nv.GetZDO() != null ? nv.GetZDO().m_uid.ToString() : null);
                    }
                    return Json.Obj("success", true, "ids", ids, "pos", Query.V(t));
                }
                case "remove":
                {
                    var nv = Query.FindById(MiniJson.Str(a, "id", ""));
                    if (nv == null) return Json.Obj("success", false, "error", "no such object");
                    nv.ClaimOwnership(); ZNetScene.instance.Destroy(nv.gameObject);
                    return Json.Obj("success", true);
                }
                case "ground":
                {
                    var t = Point(a) ?? Query.MeOrThrow().transform.position; RaycastHit h; object hit = null;
                    if (Physics.Raycast(t + Vector3.up * 50f, Vector3.down, out h, 200f, LayerMask.GetMask("Default", "static_solid", "terrain", "piece", "Default_small")))
                    {
                        var nv = h.collider.GetComponentInParent<ZNetView>();
                        hit = Query.D("collider", h.collider.name, "object", nv != null ? Utils.GetPrefabName(nv.gameObject) : null, "terrain", h.collider.GetComponent<Heightmap>() != null,
                                      "point", Query.V(h.point), "normal", Query.V(h.normal));
                    }
                    var near = new Dictionary<string, object>(a); near["r"] = MiniJson.Num(a, "r", 2.0); near["x"] = (double)t.x; near["z"] = (double)t.z;
                    return Json.Obj("ground_height", Query.R(ZoneSystem.instance.GetGroundHeight(t)), "biome", Heightmap.FindBiome(t).ToString(),
                                    "water_level", Query.R(ZoneSystem.instance.m_waterLevel), "top_hit", hit, "objects", Query.Objects(near, null)["objects"],
                                    "no_build_location", Location.IsInsideNoBuildLocation(t));
                }
                case "input_state":     // why the game ignores input right now (debugging actions)
                {
                    var p = Query.MeOrThrow();
                    bool take = (bool)AccessTools.Method(typeof(Player), "TakeInput").Invoke(p, null);
                    return Json.Obj("take_input", take, "inventory_visible", InventoryGui.IsVisible(), "menu_visible", Menu.IsVisible(), "console_visible", Console.IsVisible(),
                                    "text_viewer", TextViewer.instance != null && TextViewer.instance.IsVisible(), "minimap_open", Minimap.IsOpen(),
                                    "chat_focus", Chat.instance != null && Chat.instance.HasFocus(), "in_radial", Hud.InRadial(), "piece_selection", Hud.IsPieceSelectionVisible(),
                                    "in_attack", p.InAttack(), "in_cutscene", p.InCutscene(), "teleporting", p.IsTeleporting(), "in_place_mode", p.InPlaceMode(),
                                    "attached", p.IsAttached(), "in_emote", p.InEmote(), "encumbered", p.IsEncumbered(), "stamina", Query.R(p.GetStamina()),
                                    "right_item", p.RightItem != null ? p.RightItem.m_shared.m_name : null, "frame", Time.frameCount);
                }
                case "messages": return Json.Obj("messages", PatchShowMessage.Since(PatchShowMessage.Total - (int)MiniJson.Num(a, "n", 20)));
                case "world":
                {
                    var w = ZNet.World;
                    return Json.Obj("world", w != null ? w.m_name : null, "seed", w != null ? w.m_seedName : null, "time", ZNet.instance != null ? Math.Round(ZNet.instance.GetTimeSeconds(), 2) : 0,
                                    "load_seconds", Query.R(Session.LastLoadSeconds));
                }
                default:
                {
                    var r = Extra.EvalQuery(q, a);
                    if (r != null) return r;
                    throw new WcpException(Wcp.INVALID_PARAMS, "unknown eval_query '" + q + "' (command | status | inventory | equipment | skills | status_effects | foods | objects | pieces | containers | object | hover | recipes | build_pieces | prefabs | ground | input_state | messages | world | give | clear_inventory | teleport | spawn | remove | " + string.Join(" | ", Extra.QueryNames) + ")");
                }
            }
        }

        static string StripTags(string s) { return System.Text.RegularExpressions.Regex.Replace(s, "<[^>]+>", ""); }
    }
}
