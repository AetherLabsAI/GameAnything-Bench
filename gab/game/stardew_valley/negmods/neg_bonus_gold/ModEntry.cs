using StardewModdingAPI;
using StardewValley;

namespace NegBonusGold
{
    /// <summary>Negative mod (gab selfcheck): 1000 gold once, at the first load of a save (remembered in the player's mod data,
    /// so a reload does not pay again). Must fail SD-MONEY only.</summary>
    public class ModEntry : Mod
    {
        public override void Entry(IModHelper helper)
        {
            helper.Events.GameLoop.SaveLoaded += (s, e) =>
            {
                if (Game1.player.modData.ContainsKey("gab.neg/bonus")) return;
                Game1.player.Money += 1000;
                Game1.player.modData["gab.neg/bonus"] = "paid";
            };
        }
    }
}
