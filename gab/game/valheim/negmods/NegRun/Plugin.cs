// negative check: the player's run speed x0.6 (should fail only VH-MOVE)
using BepInEx; using HarmonyLib;
namespace NegRun {
  [BepInPlugin("ga.neg.run", "NegRun", "0.1")] public class Plugin : BaseUnityPlugin { void Awake() { new Harmony("ga.neg.run").PatchAll(); } }
  [HarmonyPatch(typeof(Player), "Awake")] static class P { static void Postfix(Player __instance) { __instance.m_runSpeed *= 0.6f; } }
}
