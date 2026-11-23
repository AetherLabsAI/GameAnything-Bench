// negative check: the Club recipe is removed from the ObjectDB (should fail only VH-CRAFT)
using BepInEx; using HarmonyLib;
namespace NegClub {
  [BepInPlugin("ga.neg.club", "NegClub", "0.1")] public class Plugin : BaseUnityPlugin { void Awake() { new Harmony("ga.neg.club").PatchAll(); } }
  [HarmonyPatch(typeof(ObjectDB), "Awake")] static class P { static void Postfix(ObjectDB __instance) { __instance.m_recipes.RemoveAll(r => r != null && r.m_item != null && r.m_item.name == "Club"); } }
}
