// STATE_SPEC self-check mod for gab selfcheck terraria --state: when the player enters a world it sets a variable the mod keeps
// (GabStateTestSystem.gabBeaconLit, gabVisits), spawns its own NPC (GabBeacon) next to the player, writes a chat line and draws a
// HUD line every frame. state() must show all of them (mod.vars, mod.npcs, screen_text chat: / hud:).
using System.Collections.Generic;
using Microsoft.Xna.Framework;
using Terraria;
using Terraria.DataStructures;
using Terraria.ModLoader;
using Terraria.UI;

namespace GabStateTest
{
    public class GabStateTest : Mod { }

    public class GabStateTestSystem : ModSystem
    {
        public bool gabBeaconLit;
        public int gabVisits;
        public static string gabQuestStage = "none";

        public override void ModifyInterfaceLayers(List<GameInterfaceLayer> layers)
        {
            layers.Add(new LegacyGameInterfaceLayer("GabStateTest: HUD", () =>
            {
                if (gabBeaconLit) Utils.DrawBorderString(Main.spriteBatch, "GAB HUD: the beacon is lit", new Vector2(40, 160), Color.Cyan, 1f);
                return true;
            }, InterfaceScaleType.UI));
        }
    }

    public class GabStateTestPlayer : ModPlayer
    {
        public override void OnEnterWorld()
        {
            var sys = ModContent.GetInstance<GabStateTestSystem>();
            sys.gabBeaconLit = true; sys.gabVisits++; GabStateTestSystem.gabQuestStage = "stage_two";
            NPC.NewNPC(new EntitySource_WorldEvent(), (int)Player.Center.X + 80, (int)Player.Center.Y, ModContent.NPCType<NPCs.GabBeacon>());
            Main.NewText("GAB chat: hello from the state test", 120, 255, 200);
        }
    }
}
