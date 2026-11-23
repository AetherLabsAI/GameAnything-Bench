// Action executor: runs one long action at a time from Update(), replies (WCP ActResult) when it finishes.
using System;
using System.Collections;
using System.Collections.Generic;
using HarmonyLib;
using UnityEngine;

namespace ValheimBridge
{
    public static class Control
    {
        static IAction current;
        static bool repauseAfter;

        public static bool Busy { get { return current != null; } }

        public static void Tick()
        {
            if (current == null) return;
            string r = null;
            try
            {
                if (Player.m_localPlayer == null) r = current.Result(false, "failed", Wcp.UNAVAILABLE, "the player left the world");
                else r = current.Step();
            }
            catch (Exception e) { r = current.Result(false, "failed", Wcp.INTERNAL, e.GetType().Name + ": " + e.Message); Plugin.Instance.Warn(e.ToString()); }
            if (r == null) return;
            var a = current; current = null;
            try { a.Finish(); } catch { }
            VInput.ReleaseAll();
            if (repauseAfter) { Game.Pause(); repauseAfter = false; }
            a.Reply(r);
        }

        public static void Start(IAction a, Action<string> ok, Action<int, string> fail)
        {
            if (current != null) { fail(Wcp.UNAVAILABLE, "busy: another action is still running (" + current.GetType().Name + ")"); return; }
            a.Reply = ok; a.Begin();
            if (Query.Paused()) { Game.Unpause(); repauseAfter = true; }
            current = a;
            Plugin.Instance.Log("start " + a.GetType().Name);
        }

        public static void Cancel(string why)
        {
            if (current == null) return;
            var a = current; current = null;
            try { a.Finish(); } catch { }
            VInput.ReleaseAll();
            a.Reply(a.Result(false, "cancelled", Wcp.ACTION_FAILED, why));
        }

        public static void SetPaused(bool on) { if (on) Game.Pause(); else Game.Unpause(); VInput.ReleaseAll(); }
    }

    public abstract class IAction
    {
        public Action<string> Reply;
        DateTime wallStart; float gameStart; int msgStart; protected float T;   // T = game seconds since start
        protected Player P { get { return Player.m_localPlayer; } }
        public abstract string Step();           // null = keep going; otherwise the ActResult JSON
        public virtual void Finish() { }
        public void Begin() { wallStart = DateTime.UtcNow; gameStart = Time.time; msgStart = PatchShowMessage.Total; }
        protected bool Tick() { T = Time.time - gameStart; return true; }

        public string Result(bool ok, string status, int errCode, string errMsg, params object[] extra)
        {
            var kv = new List<object> { "ok", ok, "status", status,
                "error", errMsg == null ? null : (object)Query.D("code", errCode, "message", errMsg),
                "ticks", Query.R(Time.time - gameStart), "wall_ms", (int)(DateTime.UtcNow - wallStart).TotalMilliseconds,
                "effects", new ArrayList(), "events", new ArrayList(PatchShowMessage.Since(msgStart)) };
            kv.AddRange(extra);
            if (Player.m_localPlayer != null) { kv.Add("player"); kv.Add(Brief()); }
            return Json.Obj(kv.ToArray());
        }
        protected string Ok(params object[] extra) { return Result(true, "done", 0, null, extra); }
        protected string Failed(string why, params object[] extra) { return Result(false, "failed", Wcp.ACTION_FAILED, why, extra); }

        public static object Brief()
        {
            var p = Player.m_localPlayer;
            return Query.D("pos", Query.V(p.transform.position), "yaw", Query.R(p.GetLookYaw().eulerAngles.y), "health", Query.R(p.GetHealth()), "stamina", Query.R(p.GetStamina()));
        }
    }

    /// Let the game run (wait / harness advance).
    public class WaitAction : IAction
    {
        readonly float dur;
        public WaitAction(float seconds) { dur = Mathf.Clamp(seconds, 0f, 120f); }
        public override string Step() { Tick(); return T < dur ? null : Ok("waited_seconds", Query.R(T)); }
    }

    /// Turn the view toward a point / a yaw+pitch (instant, like a mouse flick), then settle a few frames.
    public class LookAction : IAction
    {
        readonly Vector3? target; readonly float yaw, pitch; int f;
        public LookAction(Vector3 t) { target = t; }
        public LookAction(float yaw, float pitch) { this.yaw = yaw; this.pitch = pitch; }
        public static void Look(Player p, float yaw, float pitch)
        {
            float cy = p.GetLookYaw().eulerAngles.y, cp = Query.F<float>(p, "m_lookPitch");
            p.SetMouseLook(new Vector2(Mathf.DeltaAngle(cy, yaw), cp - pitch));
        }
        public static void LookAt(Player p, Vector3 t)
        {
            var eye = p.m_eye != null ? p.m_eye.position : p.transform.position + Vector3.up * 1.6f;
            var d = t - eye; float yaw = Mathf.Atan2(d.x, d.z) * Mathf.Rad2Deg;
            float pitch = -Mathf.Atan2(d.y, new Vector2(d.x, d.z).magnitude) * Mathf.Rad2Deg;   // Valheim: positive pitch looks down
            Look(p, yaw, pitch);
        }
        public override string Step()
        {
            if (f++ == 0) { if (target.HasValue) LookAt(P, target.Value); else Look(P, yaw, pitch); }
            return f < 4 ? null : Ok("look_yaw", Query.R(P.GetLookYaw().eulerAngles.y), "look_pitch", Query.R(Query.F<float>(P, "m_lookPitch")));
        }
    }

    /// Walk (or run) to (x, z) with the forward key, steering toward the target every frame; jump when stuck.
    public class MoveAction : IAction
    {
        readonly Vector3 target; readonly float radius, timeout; readonly bool run;
        Vector3 lastPos; float lastProgressT; int jumps;
        public MoveAction(float x, float z, float radius, bool run, float timeout) { target = new Vector3(x, 0, z); this.radius = Mathf.Max(0.3f, radius); this.run = run; this.timeout = timeout; }
        public override string Step()
        {
            Tick();
            var pos = P.transform.position; var d = target - pos; d.y = 0;
            if (d.magnitude <= radius) return Ok("distance", Query.R(d.magnitude), "jumps", jumps);
            if (T > timeout) return Failed("timeout after " + timeout + " s, " + Query.R(d.magnitude) + " m from the target", "distance", Query.R(d.magnitude), "jumps", jumps);
            float yaw = Mathf.Atan2(d.x, d.z) * Mathf.Rad2Deg;
            LookAction.Look(P, yaw, Query.F<float>(P, "m_lookPitch"));
            VInput.Hold("Forward");
            if (run) VInput.Hold("Run");
            if (T == 0 || Vector3.Distance(pos, lastPos) > 0.5f) { lastPos = pos; lastProgressT = T; }
            else if (T - lastProgressT > (P.IsRiding() ? 6f : 1.2f))
            {
                if (P.IsRiding() || P.GetControlledShip() != null)          // jumping would get off the mount: no jumps there
                    return Failed("no progress while riding, " + Query.R(d.magnitude) + " m from the target", "distance", Query.R(d.magnitude), "jumps", jumps);
                if (jumps >= 8) return Failed("stuck (no progress after " + jumps + " jumps), " + Query.R(d.magnitude) + " m from the target", "distance", Query.R(d.magnitude), "jumps", jumps);
                VInput.Press("Jump", 2); jumps++; lastProgressT = T;
            }
            return null;
        }
    }

    /// Press (or hold) a game button for a duration: attack, secondary attack, block, jump, crouch, dodge, ...
    public class ButtonAction : IAction
    {
        readonly string button; readonly float hold, settle; int f;
        public ButtonAction(string button, float holdSeconds, float settleSeconds) { this.button = button; hold = holdSeconds; settle = settleSeconds; }
        public override string Step()
        {
            Tick(); f++;
            if (T < hold || f <= 2) VInput.Hold(button);
            if (T < hold + settle || f < 4) return null;
            return Ok("button", button, "held_seconds", Query.R(hold));
        }
    }

    /// Attack with the equipped weapon (or fists) toward an optional target point; waits for the swing to end.
    public class AttackAction : IAction
    {
        readonly bool secondary; readonly Vector3? target; int f, press; bool started;
        public AttackAction(bool secondary, Vector3? target) { this.secondary = secondary; this.target = target; }
        public override string Step()
        {
            Tick(); f++;
            if (f == 1 && target.HasValue) LookAction.LookAt(P, target.Value);
            // press; if the swing has not started (equip animation, previous swing), press again (up to 3 times)
            if (!started && (f == 2 || (f - 2) % 20 == 0) && press < 3) press++;
            if (!started && f >= 2 && (f - 2) % 20 < 3) VInput.Hold(secondary ? "SecondaryAttack" : "Attack");
            if (P.InAttack()) started = true;
            if (!started) return f < 62 ? null : Failed("the attack did not start after " + press + " presses (stamina? in a menu? swimming?)", "attacked", false);
            if (P.InAttack() && T < 4f) return null;
            return Ok("attacked", true, "presses", press);
        }
    }

    /// Interact ("use", E) with the hovered object, or aim at a given object / point first. With a target farther than
    /// `reach` (default 1.5 m, horizontal; crafting stations need < 2 m) the player first walks up to it (approach=false: fail instead).
    public class UseAction : IAction
    {
        readonly Vector3? aim; readonly string wantId; readonly bool hold, approach; readonly float reach; int f; object hovered; MoveAction walk; bool walked; string tool;
        public UseAction(Vector3? aim, string id, bool hold, bool approach = true, float reach = 1.5f) { this.aim = aim; wantId = id; this.hold = hold; this.approach = approach; this.reach = reach; }
        public override string Step()
        {
            Tick();
            if (aim.HasValue && f == 0)
            {
                var d = aim.Value - P.transform.position; d.y = 0;
                if (d.magnitude > reach)
                {
                    if (!approach) return Failed("the target is " + Query.R(d.magnitude) + " m away (interact range ~" + Query.R(P.m_maxInteractDistance) + " m)");
                    if (walk == null) { walk = new MoveAction(aim.Value.x, aim.Value.z, reach * 0.8f, false, 20f); walk.Begin(); walked = true; }
                    var r = walk.Step();
                    if (r == null) return null;
                    VInput.ReleaseAll();
                    if (r.Contains("\"ok\":false")) return Failed("could not walk up to the target", "walk", new Json.Raw(r));
                    walk = null;
                }
            }
            f++;
            // build mode (hammer in hand) disables hovering: put the build tool away first, as a player would
            if (f == 1 && P.InPlaceMode() && P.RightItem != null) { tool = P.RightItem.m_shared.m_name; P.UnequipItem(P.RightItem); }
            if (aim.HasValue) { VInput.AimTarget = aim; if (f == 1) LookAction.LookAt(P, aim.Value); }
            if (f < 4) return null;                            // let UpdateHover run with the aim
            if (f == 4)
            {
                var h = P.GetHoverObject();
                hovered = h != null ? (object)Query.HoverInfo(h) : null;
                if (h == null) return Failed("nothing to interact with under the cursor" + (aim.HasValue ? " at the target (blocked or too far: interact range ~" + Query.R(P.m_maxInteractDistance) + " m)" : ""));
                if (wantId != null)
                {
                    var nv = h.GetComponentInParent<ZNetView>();
                    if (nv == null || nv.GetZDO() == null || nv.GetZDO().m_uid.ToString() != wantId)
                        return Failed("the cursor is on another object (" + Utils.GetPrefabName(nv != null ? nv.gameObject : h) + "): the target is occluded", "hovered", hovered);
                }
            }
            if (f <= 4 + (hold ? 40 : 2)) { VInput.Hold("Use"); return null; }
            if (f < 4 + (hold ? 40 : 2) + 10) return null;
            var st = P.GetCurrentCraftingStation();
            return Ok("hovered", hovered, "walked", walked, "put_away", tool, "inventory_open", InventoryGui.IsVisible(), "crafting_station", st != null ? Utils.GetPrefabName(st.gameObject) : null);
        }
        public override void Finish() { VInput.AimTarget = null; }
    }

    /// Place a build piece with the hammer (or another build tool) at a world point: equip the tool, select the piece,
    /// aim at the point, press attack (the game checks resources, placement rules and consumes the cost).
    public class PlaceAction : IAction
    {
        readonly string prefab; readonly Vector3 at; readonly float yaw; int f; Piece piece; string why; int before; ItemDrop.ItemData tool;
        bool terrain; float h0; bool cult0; float st0;     // terrain tools (hoe / cultivator ops): no new object, the ground changes
        public PlaceAction(string prefab, Vector3 at, float yaw) { this.prefab = prefab; this.at = at; this.yaw = yaw; }

        int CountNear() { int n = 0; foreach (var nv in Query.Instances()) if (Utils.GetPrefabName(nv.gameObject) == Utils.GetPrefabName(piece.gameObject) && Vector3.Distance(nv.transform.position, at) < 3f) n++; return n; }
        // note: tool pieces may be absent from ZNetScene; the tool lookup below uses the inventory

        public override string Step()
        {
            Tick(); f++;
            if (f == 1)
            {
                var go = ZNetScene.instance.GetPrefab(prefab);
                if (go == null)          // hoe / cultivator terrain ops (cultivate_v2, raise_v2 ...) live only in the tools' piece tables
                    foreach (var it in P.GetInventory().GetAllItems())
                        if (it.m_shared.m_buildPieces != null) { var g = it.m_shared.m_buildPieces.m_pieces.Find(x => x != null && x.name == prefab); if (g != null) { go = g; break; } }
                piece = go != null ? go.GetComponent<Piece>() : null;
                if (piece == null) return Failed("no build piece prefab '" + prefab + "'");
                // the build tool whose piece table holds this piece
                foreach (var it in P.GetInventory().GetAllItems())
                    if (it.m_shared.m_buildPieces != null && it.m_shared.m_buildPieces.m_pieces.Contains(piece.gameObject)) { tool = it; break; }
                if (tool == null) return Failed("no build tool in the inventory can place " + prefab + " (hammer / hoe / cultivator)");
                if (P.RightItem != tool) P.EquipItem(tool);
                return null;
            }
            if (f == 2)
            {
                if (!P.InPlaceMode()) return Failed("could not enter build mode with " + tool.m_shared.m_name);
                if (!P.IsPieceAvailable(piece)) return Failed(prefab + " is not available in the build menu (unknown recipe / missing station or resources seen)");
                if (!P.SetSelectedPiece(piece)) return Failed("could not select " + prefab);
                Traverse.Create(P).Field("m_placeRotation").SetValue(Mathf.RoundToInt(yaw / 22.5f) & 15);
                VInput.AimTarget = at; before = CountNear();
                terrain = piece.GetComponent<TerrainOp>() != null;
                if (terrain) { h0 = ZoneSystem.instance.GetGroundHeight(at); var hm = Heightmap.FindHeightmap(at); cult0 = hm != null && hm.IsCultivated(at); st0 = P.GetStamina(); }
                return null;
            }
            VInput.AimTarget = at;
            if (f < 6) return null;
            if (f == 6)
            {
                var st = P.GetPlacementStatus();
                var ghost = Query.F<GameObject>(P, "m_placementGhost");
                why = st.ToString();
                if (st != Player.PlacementStatus.Valid)
                    return Failed("placement not valid: " + st, "placement_status", why, "ghost_pos", ghost != null ? Query.V(ghost.transform.position) : null, "ray", RayInfo());
                if (!P.NoCostCheat() && !P.HaveRequirements(piece, Player.RequirementMode.CanBuild))
                    return Failed("missing resources for " + prefab, "placement_status", why, "piece", Query.PieceInfo(piece, P));
            }
            if (f >= 6 && f <= 8) { VInput.Hold("Attack"); return null; }
            if (f < 20) return null;
            if (terrain)
            {
                if (f < 40) return null;       // terrain edits apply over a few frames
                float h1 = ZoneSystem.instance.GetGroundHeight(at); var hm = Heightmap.FindHeightmap(at); bool cult1 = hm != null && hm.IsCultivated(at);
                bool changed = Mathf.Abs(h1 - h0) > 0.01f || cult1 != cult0 || P.GetStamina() < st0 - 0.5f;
                var tr = Query.D("height_before", Query.R(h0), "height_after", Query.R(h1), "cultivated_before", cult0, "cultivated_after", cult1);
                return changed ? Ok("placement_status", why, "terrain", tr) : Failed("pressed the terrain tool but the ground did not change", "placement_status", why, "terrain", tr);
            }
            int after = CountNear();
            if (after > before)
            {
                object placed = null; float best = 1e9f;
                foreach (var nv in Query.Instances())
                    if (Utils.GetPrefabName(nv.gameObject) == Utils.GetPrefabName(piece.gameObject)) { float dd = Vector3.Distance(nv.transform.position, at); if (dd < best) { best = dd; placed = Query.Obj(nv, at, false); } }
                return Ok("placement_status", why, "placed", placed);
            }
            return Failed("pressed place but no new " + prefab + " appeared", "placement_status", why);
        }
        public override void Finish() { VInput.AimTarget = null; }

        /// What the aimed placement ray hits (camera -> target): the collider decides ground / piece / blocked.
        object RayInfo()
        {
            var cam = GameCamera.instance; if (cam == null) return null;
            int mask = Query.F<int>(P, "m_placeRayMask");
            var o = cam.transform.position; var d = (at - o).normalized; RaycastHit h;
            if (!Physics.Raycast(o, d, out h, 50f, mask)) return Query.D("hit", null);
            var nv = h.collider.GetComponentInParent<ZNetView>();
            return Query.D("hit", h.collider.name, "object", nv != null ? Utils.GetPrefabName(nv.gameObject) : null, "terrain", h.collider.GetComponent<Heightmap>() != null,
                           "point", Query.V(h.point), "normal", Query.V(h.normal), "from_target", Query.R(Vector3.Distance(h.point, at)),
                           "eye_dist", Query.R(Vector3.Distance(P.m_eye.position, h.point)), "max_place_dist", Query.R(P.m_maxPlaceDistance));
        }
    }
}
