using StardewModdingAPI;
using StardewValley;

namespace NegSlowClock
{
    /// <summary>Negative mod (gab selfcheck): 10 in-game minutes take 14 s instead of 7 s. Must fail SD-CLOCK only.</summary>
    public class ModEntry : Mod
    {
        public override void Entry(IModHelper helper)
        {
            helper.Events.GameLoop.SaveLoaded += (s, e) => Slow();
            helper.Events.GameLoop.DayStarted += (s, e) => Slow();
        }

        private static void Slow()
        {
            Game1.realMilliSecondsPerGameMinute = 1400;
            Game1.realMilliSecondsPerGameTenMinutes = 14000;
        }
    }
}
