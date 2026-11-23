// GameAnything-Bench Terraria template mod (tModLoader 2026.08, Terraria 1.4.4.9). Rename the folder, the namespace and the class
// together: folder name = mod name = namespace = the Mod class. It shows the usual pieces:
//   - a chat line when the player enters a world (ModPlayer.OnEnterWorld + Main.NewText),
//   - a banner drawn over the game every frame (ModSystem.ModifyInterfaceLayers),
//   - a new item with a texture and a recipe (Items/GabSword.cs + Items/GabSword.png, names in Localization/).
using System.Collections.Generic;
using Microsoft.Xna.Framework;
using Terraria;
using Terraria.ModLoader;
using Terraria.UI;

namespace GabTemplate
{
    public class GabTemplate : Mod
    {
        public override void Load() => Logger.Info("GabTemplate loaded");
    }

    public class GabTemplatePlayer : ModPlayer
    {
        public override void OnEnterWorld() => Main.NewText("GabTemplate: hello, " + Player.name + "!", 255, 220, 80);
    }

    public class GabTemplateBanner : ModSystem
    {
        public override void ModifyInterfaceLayers(List<GameInterfaceLayer> layers)
        {
            int i = layers.FindIndex(l => l.Name == "Vanilla: Mouse Text");
            layers.Insert(i < 0 ? layers.Count : i, new LegacyGameInterfaceLayer("GabTemplate: Banner", () =>
            {
                Utils.DrawBorderString(Main.spriteBatch, "GabTemplate is running", new Vector2(Main.screenWidth / 2f - 110, 110), Color.Gold, 1.2f);
                return true;
            }, InterfaceScaleType.UI));
        }
    }
}
