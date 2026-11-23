using System.Collections.Generic;
using Microsoft.Xna.Framework;
using Microsoft.Xna.Framework.Graphics;
using StardewModdingAPI;
using StardewModdingAPI.Events;
using StardewValley;
using StardewValley.GameData.Objects;
using SObject = StardewValley.Object;

namespace GabStateCheck
{
    /// <summary>gab STATE_SPEC self-check mod: sets a mod variable (player mod data + a mail flag), adds an item to Data/Objects,
    /// spawns a new character in the farmhouse, places a marked object, and shows HUD text (a HUD message + its own drawn line).</summary>
    public class ModEntry : Mod
    {
        public override void Entry(IModHelper helper)
        {
            helper.Events.Content.AssetRequested += (s, e) =>
            {
                if (e.NameWithoutLocale.IsEquivalentTo("Data/Objects"))
                    e.Edit(a => a.AsDictionary<string, ObjectData>().Data["GabStateCheck_Lantern"] = new ObjectData
                    { Name = "GabStateCheck_Lantern", DisplayName = "Beacon Lantern", Description = "gab state check", Type = "Basic", Category = 0, Price = 1, Texture = "Maps/springobjects", SpriteIndex = 93 });
            };
            helper.Events.GameLoop.SaveLoaded += (s, e) =>
            {
                Game1.player.modData["gab.statecheck/beacon"] = "lit";
                Game1.player.mailReceived.Add("GabStateCheck_BeaconLit");
                var home = Game1.getLocationFromName("FarmHouse");
                var npc = new NPC(new AnimatedSprite("Characters\\Abigail", 0, 16, 32), new Vector2(5 * 64, 8 * 64), "FarmHouse", 2, "GabBeaconKeeper", false, null);
                home.addCharacter(npc);
                var o = ItemRegistry.Create<SObject>("(O)GabStateCheck_Lantern");
                o.modData["gab.statecheck/marker"] = "signpost";
                home.objects[new Vector2(4, 9)] = o;
            };
            helper.Events.GameLoop.OneSecondUpdateTicked += (s, e) =>
            {
                if (Context.IsWorldReady && e.IsMultipleOf(120)) Game1.addHUDMessage(new HUDMessage("GAB HUD: the beacon keeper waits nearby"));
            };
            helper.Events.GameLoop.DayStarted += (s, e) => Game1.addHUDMessage(new HUDMessage("GAB HUD: the beacon keeper waits nearby"));
            helper.Events.Display.RenderedHud += (s, e) =>
                e.SpriteBatch.DrawString(Game1.smallFont, "GAB quest: talk to the beacon keeper", new Vector2(24, 640), Color.White);
        }
    }
}
