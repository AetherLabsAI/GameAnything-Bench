// Virtual input: our own button states OR-ed into ZInput.GetButton / GetButtonDown / GetButtonUp (Harmony postfixes),
// so every action goes through the game's normal input path (PlayerController, Player.Update, UpdatePlacement, ...).
// Aim: while an aim target is set, the camera ray used for hovering (Player.FindHoverObject) and for build placement
// (Player.PieceRayTest) is pointed at that world point, as if the player had moved the mouse onto it.
using System.Collections.Generic;
using HarmonyLib;
using UnityEngine;

namespace ValheimBridge
{
    public static class VInput
    {
        class Btn { public int Start, End; }   // held on frames [Start, End]; down on Start; up on End + 1
        static readonly Dictionary<string, Btn> buttons = new Dictionary<string, Btn>();
        public static Vector3? AimTarget;

        /// Press `name` for `frames` frames, starting next frame.
        public static void Press(string name, int frames = 2)
        {
            int f = Time.frameCount + 1;
            buttons[name] = new Btn { Start = f, End = f + Mathf.Max(1, frames) - 1 };
        }

        /// Keep `name` held (call every frame; the press starts next frame if it was not held).
        public static void Hold(string name)
        {
            Btn b; int f = Time.frameCount;
            if (buttons.TryGetValue(name, out b) && b.End >= f - 1 && b.Start <= f + 1) { b.End = f + 1; return; }
            buttons[name] = new Btn { Start = f + 1, End = f + 1 };
        }

        public static void Release(string name)
        {
            Btn b; int f = Time.frameCount;
            if (buttons.TryGetValue(name, out b) && b.End >= f) b.End = Mathf.Max(b.Start - 1, f - 1);
        }

        public static void ReleaseAll()
        {
            foreach (var k in new List<string>(buttons.Keys)) Release(k);
            AimTarget = null;
        }

        public static bool Held(string n) { Btn b; int f = Time.frameCount; return buttons.TryGetValue(n, out b) && f >= b.Start && f <= b.End; }
        public static bool Down(string n) { Btn b; return buttons.TryGetValue(n, out b) && Time.frameCount == b.Start; }
        public static bool Up(string n) { Btn b; return buttons.TryGetValue(n, out b) && Time.frameCount == b.End + 1; }
    }

    [HarmonyPatch(typeof(ZInput), nameof(ZInput.GetButton))]
    static class PatchGetButton { static void Postfix(string name, ref bool __result) { if (!__result && VInput.Held(name)) __result = true; } }

    [HarmonyPatch(typeof(ZInput), nameof(ZInput.GetButtonDown))]
    static class PatchGetButtonDown { static void Postfix(string name, ref bool __result) { if (!__result && VInput.Down(name)) __result = true; } }

    [HarmonyPatch(typeof(ZInput), nameof(ZInput.GetButtonUp))]
    static class PatchGetButtonUp { static void Postfix(string name, ref bool __result) { if (!__result && VInput.Up(name)) __result = true; } }

    /// Point the camera at the aim target for the duration of a ray test, then restore it.
    static class Aim
    {
        public static void Prefix(ref Quaternion __state)
        {
            __state = Quaternion.identity;
            var cam = GameCamera.instance;
            if (cam == null || !VInput.AimTarget.HasValue) return;
            __state = cam.transform.rotation;
            var d = VInput.AimTarget.Value - cam.transform.position;
            if (d.sqrMagnitude > 1e-6f) cam.transform.rotation = Quaternion.LookRotation(d.normalized);
        }
        public static void Postfix(Quaternion __state)
        {
            var cam = GameCamera.instance;
            if (cam == null || !VInput.AimTarget.HasValue) return;
            cam.transform.rotation = __state;
        }
    }

    [HarmonyPatch(typeof(Player), "FindHoverObject")]
    static class PatchHover
    {
        static void Prefix(ref Quaternion __state) { Aim.Prefix(ref __state); }
        static void Postfix(Quaternion __state) { Aim.Postfix(__state); }
    }

    [HarmonyPatch(typeof(Player), "PieceRayTest")]
    static class PatchPieceRay
    {
        static void Prefix(ref Quaternion __state) { Aim.Prefix(ref __state); }
        static void Postfix(Quaternion __state) { Aim.Postfix(__state); }
    }

    /// Capture console output while a probe command runs.
    [HarmonyPatch(typeof(Terminal), nameof(Terminal.AddString), new[] { typeof(string) })]
    static class PatchAddString
    {
        public static List<string> Capture;
        static void Prefix(string text) { if (Capture != null) Capture.Add(text); }
    }

    /// Recent HUD messages ("$msg_needspace", pickups, ...): reported as action events.
    [HarmonyPatch(typeof(MessageHud), nameof(MessageHud.ShowMessage))]
    static class PatchShowMessage
    {
        public static readonly List<string> Recent = new List<string>();
        public static int Total;
        static void Prefix(MessageHud.MessageType type, string text, int amount)
        {
            string t = text;
            try { t = Localization.instance.Localize(text); } catch { }
            if (amount > 1) t += " x" + amount;
            lock (Recent) { Recent.Add(t); Total++; if (Recent.Count > 50) Recent.RemoveAt(0); }
        }
        public static List<string> Since(int total)
        {
            lock (Recent) { int n = Mathf.Clamp(Total - total, 0, Recent.Count); return Recent.GetRange(Recent.Count - n, n); }
        }
    }
}
