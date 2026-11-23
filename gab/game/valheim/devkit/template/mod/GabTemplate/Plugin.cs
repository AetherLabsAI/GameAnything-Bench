// GameAnything-Bench Valheim template plugin (BepInEx 5, Harmony, net462 against the game's own assemblies). Rename the folder, the
// .csproj (AssemblyName / RootNamespace), the namespace and the BepInPlugin GUID / name together. It shows the usual pieces:
//   - a Harmony postfix on Player.OnSpawned: a centre message when the player appears in the world,
//   - an Update loop: F7 shows a top-left message and counts the presses in the player's custom data (saved with the character),
//   - its folder: files next to the DLL (assets/) are found from Info.Location.
using System.IO;
using BepInEx;
using HarmonyLib;
using UnityEngine;

namespace GabTemplate
{
    [BepInPlugin("gab.template", "GabTemplate", "0.1.0")]
    public class Plugin : BaseUnityPlugin
    {
        public static string Dir;

        void Awake()
        {
            Dir = Path.GetDirectoryName(Info.Location);
            new Harmony("gab.template").PatchAll();
            Logger.LogInfo("GabTemplate loaded from " + Dir);
        }

        void Update()
        {
            var p = Player.m_localPlayer;
            if (p == null || !Input.GetKeyDown(KeyCode.F7)) return;
            p.m_customData.TryGetValue("gab_template_presses", out var s);
            int n = (int.TryParse(s, out var k) ? k : 0) + 1;
            p.m_customData["gab_template_presses"] = n.ToString();
            p.Message(MessageHud.MessageType.TopLeft, "GabTemplate: F7 pressed " + n + " times");
        }
    }

    [HarmonyPatch(typeof(Player), nameof(Player.OnSpawned))]
    static class OnSpawned
    {
        static void Postfix(Player __instance)
        {
            if (__instance == Player.m_localPlayer) MessageHud.instance?.ShowMessage(MessageHud.MessageType.Center, "GabTemplate is running");
        }
    }
}
