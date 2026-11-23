// Session control without menus: create/select a character and a world, start it as a local (closed) server, wait for
// the player to spawn; leave the world back to the start scene. Steps run from Update() as a small state machine.
using System;
using System.Collections.Generic;
using HarmonyLib;
using UnityEngine;

namespace ValheimBridge
{
    public static class Session
    {
        public static string Phase = "idle";        // idle | logout | menu_wait | starting | loading | in_world | failed
        public static string Error;
        static string wantWorld, wantSeed, wantChar, wantTemplate; static bool wantFresh, wantCheats = true;
        static float phaseAt, startedAt, timeout = 1200; public static float LastLoadSeconds = -1;
        public static string LastRestored;
        static Action<string> pendingOk;
        static float seenAt = -1, calmSince = -1;

        public static string Status()
        {
            var p = Player.m_localPlayer;
            if (Phase != "idle" && Phase != "in_world" && Phase != "failed") return "loading";
            if (p != null && Game.instance != null && !Game.instance.IsShuttingDown()) return p.IsDead() ? "dead" : "in_world";
            if (FejdStartup.instance != null) return "menu";
            if (Query.Scene() == "main") return "loading";
            return "booting";
        }

        /// reset: {world, seed, character, fresh (delete + recreate the world; default true), template (restore world + character
        /// files saved by save_template first: fast, no world generation), cheats (devcommands on, default true),
        /// wait (reply when spawned; default true; false = reply now and poll health), timeout (s, default 1200)}.
        public static void Start(Dictionary<string, object> s, Action<string> ok, Action<int, string> fail)
        {
            if (Phase != "idle" && Phase != "in_world" && Phase != "failed") { fail(Wcp.UNAVAILABLE, "busy: a session change is in progress (" + Phase + ")"); return; }
            wantWorld = MiniJson.Str(s, "world", "wcbench");
            wantSeed = MiniJson.Str(s, "seed", "wcbench01");
            wantChar = MiniJson.Str(s, "character", "wcagent").ToLowerInvariant();
            wantFresh = !(s.ContainsKey("fresh") && s["fresh"] is bool && !(bool)s["fresh"]);
            wantCheats = !(s.ContainsKey("cheats") && s["cheats"] is bool && !(bool)s["cheats"]);
            wantTemplate = MiniJson.Str(s, "template", null);
            if (wantTemplate != null) { wantFresh = false; if (!Templates.Has(wantTemplate)) { fail(Wcp.INVALID_PARAMS, "no template '" + wantTemplate + "' (save_template first)"); return; }
                                         wantWorld = Templates.Part(wantTemplate, ".fwl") ?? wantWorld; wantChar = Templates.Part(wantTemplate, ".fch") ?? wantChar; }
            timeout = (float)MiniJson.Num(s, "timeout", 1200);
            bool wait = !(s.ContainsKey("wait") && s["wait"] is bool && !(bool)s["wait"]);
            Error = null; startedAt = Time.realtimeSinceStartup; LastRestored = null; seenAt = -1; calmSince = -1;
            pendingOk = wait ? ok : null;
            if (!wait) ok(Json.Obj("ok", true, "status", "loading", "world", wantWorld, "template", wantTemplate));
            Control.Cancel("session reset");
            if (Game.instance != null && Player.m_localPlayer != null) { Go("logout"); Game.instance.Logout(true, true); }
            else Go("menu_wait");
        }

        /// leave: back to the main menu (saves).
        public static void Leave()
        {
            Control.Cancel("leave");
            if (Game.instance != null) Game.instance.Logout(true, true);
            Phase = "idle";
        }

        static void Go(string ph) { Phase = ph; phaseAt = Time.realtimeSinceStartup; Plugin.Instance.Log("session: " + ph); }
        static float InPhase { get { return Time.realtimeSinceStartup - phaseAt; } }

        static void Fail(string why)
        {
            Error = why; Go("failed"); Plugin.Instance.Warn("session failed: " + why);
            var ok = pendingOk; pendingOk = null;
            if (ok != null) ok(Json.Obj("ok", false, "status", "failed", "error", why));
        }

        public static void Tick()
        {
            switch (Phase)
            {
                case "logout":
                    if (FejdStartup.instance != null && Player.m_localPlayer == null) Go("menu_wait");
                    else if (InPhase > 120) Fail("logout did not return to the start scene in 120 s");
                    return;
                case "menu_wait":
                    // the start scene needs a moment (Start(): GUI, ObjectDB, profiles); PlayFab login is not needed for a closed local server
                    if (FejdStartup.instance != null && InPhase > 1.5f && Query.F<bool>(FejdStartup.instance, "m_startingWorld") == false)
                    {
                        try { BeginWorld(); Go("starting"); } catch (Exception e) { Fail("start failed: " + e); }
                    }
                    else if (InPhase > 240) Fail("no start scene (FejdStartup) after 240 s");
                    return;
                case "starting":
                case "loading":
                    var p = Player.m_localPlayer;
                    if (Phase == "starting" && FejdStartup.instance == null) Go("loading");
                    // ready = the player exists, its area is loaded and it takes input (the spawn "wake up" animation is a
                    // cutscene that starts a few frames AFTER the spawn) continuously for 1.5 s
                    bool calm = p != null && Game.instance != null && !p.IsTeleporting() && Query.Scene() == "main" && ZNetScene.instance != null
                                && ZNetScene.instance.IsAreaReady(p.transform.position) && !p.InCutscene()
                                && (bool)AccessTools.Method(typeof(Player), "TakeInput").Invoke(p, null);
                    if (p != null && seenAt < 0) seenAt = Time.realtimeSinceStartup;
                    if (!calm) calmSince = -1; else if (calmSince < 0) calmSince = Time.realtimeSinceStartup;
                    bool ready = calm && Time.realtimeSinceStartup - calmSince > 1.5f && Time.realtimeSinceStartup - seenAt > 3f;
                    if (!ready && p != null && seenAt > 0 && Time.realtimeSinceStartup - seenAt > 90) ready = true;   // never stuck on this check
                    if (ready)
                    {
                        if (wantCheats) EnableCheats();
                        LastLoadSeconds = Time.realtimeSinceStartup - startedAt;
                        Go("in_world");
                        var ok = pendingOk; pendingOk = null;
                        if (ok != null) ok(Json.Obj("ok", true, "status", "in_world", "world", wantWorld, "seed", wantSeed, "character", wantChar,
                                                    "template", wantTemplate, "load_seconds", Query.R(LastLoadSeconds), "player", Query.Status()));
                    }
                    else if (Time.realtimeSinceStartup - startedAt > timeout) Fail("player did not spawn within " + timeout + " s (phase " + Phase + ", scene " + Query.Scene() + ")");
                    return;
            }
        }

        public static void EnableCheats()
        {
            Terminal.m_cheat = true;
            // the first cheat command asks for confirmation ("confirmcheats") on a fresh character
            if (Console.instance != null && !Achievements.IsCheatedAtAll()) Console.instance.TryRunCommand("confirmcheats", true, true);
        }

        static void BeginWorld()
        {
            var fs = FejdStartup.instance;
            PlatformPrefs.SetInt("SkipIntroCinematic", 1);
            // character: a local profile; no intro flight (m_firstSpawn = false), default items as for a new character
            if (!PlayerProfile.HaveProfile(wantChar))
            {
                var prof = new PlayerProfile(wantChar, FileHelpers.FileSource.Local);
                prof.SetName(wantChar);
                prof.m_firstSpawn = false;
                prof.Save();
                SaveSystem.InvalidateCache(SaveDataType.Character);
                Plugin.Instance.Log("created character " + wantChar);
            }
            if (wantTemplate != null) LastRestored = Templates.Restore(wantTemplate, wantWorld, wantChar);
            // world: local; fresh = delete and recreate with the given seed
            if (wantFresh && World.HaveWorld(wantWorld)) { World.RemoveWorld(wantWorld, FileHelpers.FileSource.Local); SaveSystem.InvalidateCache(SaveDataType.World); }
            World world;
            if (!World.HaveWorld(wantWorld))
            {
                world = new World(wantWorld, wantSeed);
                world.m_fileSource = FileHelpers.FileSource.Local;
                world.m_needsDB = false;
                SaveSystem.SetSaveNumber(0u);
                world.SaveWorldFWLData(DateTime.Now);
                SaveSystem.InvalidateCache(SaveDataType.World);
                Plugin.Instance.Log("created world " + wantWorld + " seed " + wantSeed);
            }
            else world = World.GetCreateWorld(wantWorld, FileHelpers.FileSource.Local);
            // a reused character must not replay the intro either
            PlatformPrefs.SetString("profile", wantChar);
            Game.SetProfile(wantChar, FileHelpers.FileSource.Local);
            // start as a closed local server (no PlayFab, no server listing): what the menu's "Start" does with "Start server" off
            Traverse.Create(fs).Field("m_world").SetValue(world);
            fs.m_openServerToggle.isOn = false; fs.m_publicServerToggle.isOn = false; fs.m_crossplayServerToggle.isOn = false;
            fs.m_serverPassword.text = "";
            AccessTools.Method(typeof(FejdStartup), "OnWorldStart").Invoke(fs, null);
            if (!Query.F<bool>(fs, "m_startingWorld")) throw new Exception("OnWorldStart did not start the world (popup? data error " + world.m_dataError + ")");
        }
    }

    /// A reused character profile whose first spawn has not happened yet would replay the valkyrie intro: skip it.
    [HarmonyPatch(typeof(PlayerProfile), nameof(PlayerProfile.Load))]
    static class PatchProfileLoad
    {
        static void Postfix(PlayerProfile __instance) { if (Session.Phase == "starting" || Session.Phase == "loading" || Session.Phase == "menu_wait") __instance.m_firstSpawn = false; }
    }

    /// World + character snapshots: <save root>/wc_templates/<name>/{world/<world>/..., <character>.fch}.
    /// Valheim 1.0 saves a world as a directory (worlds_local/<world>/: _main.N.fwl2, chunks, db2, minimap caches).
    /// save_template saves the running world synchronously and copies its directory; reset {template} copies it back
    /// (in the start scene, before the world is loaded), so a test starts from the same state in seconds.
    public static class Templates
    {
        static string Root { get { return System.IO.Path.Combine(Utils.GetSaveDataPath(FileHelpers.FileSource.Local), "wc_templates"); } }
        static string Worlds { get { return SaveSystem.GetWorldsSaveRootPath(FileHelpers.FileSource.Local); } }
        static string Chars { get { return SaveSystem.GetCharacterFolderPath(FileHelpers.FileSource.Local); } }

        public static bool Has(string name) { return System.IO.Directory.Exists(System.IO.Path.Combine(Root, name, "world")); }

        public static string Part(string name, string ext)
        {
            string dir = System.IO.Path.Combine(Root, name);
            if (ext == ".fwl") { foreach (var d in System.IO.Directory.GetDirectories(System.IO.Path.Combine(dir, "world"))) return System.IO.Path.GetFileName(d); return null; }
            foreach (var f in System.IO.Directory.GetFiles(dir, "*" + ext)) return System.IO.Path.GetFileNameWithoutExtension(f);
            return null;
        }

        static void CopyDir(string src, string dst)
        {
            System.IO.Directory.CreateDirectory(dst);
            foreach (var f in System.IO.Directory.GetFiles(src)) System.IO.File.Copy(f, System.IO.Path.Combine(dst, System.IO.Path.GetFileName(f)), true);
            foreach (var d in System.IO.Directory.GetDirectories(src)) CopyDir(d, System.IO.Path.Combine(dst, System.IO.Path.GetFileName(d)));
        }

        static long Size(string dir) { long n = 0; foreach (var f in System.IO.Directory.GetFiles(dir, "*", System.IO.SearchOption.AllDirectories)) n += new System.IO.FileInfo(f).Length; return n; }

        /// keepPosition (bridge 0.2): store the player's position as the logout point, so the template starts there
        /// (default false: the player starts at the world spawn / its bed, as in 0.1).
        public static string Save(string name, bool keepPosition = false)
        {
            var p = Player.m_localPlayer; var w = ZNet.World;
            if (p == null || w == null) throw new WcpException(Wcp.NOT_READY, "not in a world");
            Game.instance.SavePlayerProfile(keepPosition);
            ZNet.instance.Save(true, false, false);
            string dir = System.IO.Path.Combine(Root, name), wsrc = w.GetSaveDirectory(FileHelpers.FileSource.Local).TrimEnd('/');
            if (!System.IO.Directory.Exists(wsrc)) throw new WcpException(Wcp.INTERNAL, "world save dir missing after save: " + wsrc);
            if (System.IO.Directory.Exists(dir)) System.IO.Directory.Delete(dir, true);
            CopyDir(wsrc, System.IO.Path.Combine(dir, "world", w.m_name));
            string fn = Game.instance.GetPlayerProfile().GetFilename();
            string ch = Chars + fn + ".fch";
            if (System.IO.File.Exists(ch)) System.IO.File.Copy(ch, System.IO.Path.Combine(dir, fn + ".fch"));
            return Json.Obj("template", name, "world", w.m_name, "character", fn, "bytes", Size(dir), "dir", dir);
        }

        public static string Restore(string name, string world, string character)
        {
            string dir = System.IO.Path.Combine(Root, name); var done = new List<string>();
            foreach (var wd in System.IO.Directory.GetDirectories(System.IO.Path.Combine(dir, "world")))
            {
                string dst = System.IO.Path.Combine(Worlds, System.IO.Path.GetFileName(wd));
                if (System.IO.Directory.Exists(dst)) System.IO.Directory.Delete(dst, true);
                CopyDir(wd, dst); done.Add("world/" + System.IO.Path.GetFileName(wd));
            }
            foreach (var f in System.IO.Directory.GetFiles(dir, "*.fch")) { System.IO.File.Copy(f, Chars + System.IO.Path.GetFileName(f), true); done.Add(System.IO.Path.GetFileName(f)); }
            SaveSystem.InvalidateCache(SaveDataType.World); SaveSystem.InvalidateCache(SaveDataType.Character);
            Plugin.Instance.Log("restored template " + name + ": " + string.Join(", ", done.ToArray()));
            return string.Join(", ", done.ToArray());
        }
    }
}
