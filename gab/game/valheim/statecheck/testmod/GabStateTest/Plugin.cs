// STATE_SPEC self-check plugin for gab selfcheck valheim --state: when the local player spawns it sets a global key (gab_test_flag) and
// player custom data (gab_test_stage = stage_two), places its own networked object (gab_beacon_chest: a runtime copy of the wooden chest
// registered under a new prefab name) 3 m in front of the player and shows a centre message. state() must show all of them
// (mod.global_keys, mod.player_data, mod.objects, screen_text hud:).
using BepInEx;
using HarmonyLib;
using UnityEngine;

namespace GabStateTest
{
    [BepInPlugin("gab.statetest", "GabStateTest", "0.1.0")]
    public class Plugin : BaseUnityPlugin
    {
        void Awake() { new Harmony("gab.statetest").PatchAll(); }

        public static GameObject Beacon()
        {
            var zs = ZNetScene.instance; int h = "gab_beacon_chest".GetStableHashCode();
            var have = zs.GetPrefab(h);
            if (have != null) return have;
            var src = zs.GetPrefab("piece_chest_wood");
            var holder = new GameObject("gab_prefabs"); holder.SetActive(false); Object.DontDestroyOnLoad(holder);
            var clone = Object.Instantiate(src, holder.transform); clone.name = "gab_beacon_chest";
            zs.m_prefabs.Add(clone);
            Traverse.Create(zs).Field("m_namedPrefabs").GetValue<System.Collections.Generic.Dictionary<int, GameObject>>()[h] = clone;
            return clone;
        }
    }

    [HarmonyPatch(typeof(Player), nameof(Player.OnSpawned))]
    static class OnSpawned
    {
        static void Postfix(Player __instance)
        {
            if (__instance != Player.m_localPlayer) return;
            ZoneSystem.instance.SetGlobalKey("gab_test_flag");
            __instance.m_customData["gab_test_stage"] = "stage_two";
            var pos = __instance.transform.position + __instance.transform.forward * 3f;
            pos.y = ZoneSystem.instance.GetGroundHeight(pos);
            Object.Instantiate(Plugin.Beacon(), pos, Quaternion.identity);
            MessageHud.instance?.ShowMessage(MessageHud.MessageType.Center, "GAB HUD: the beacon is lit");
        }
    }
}
