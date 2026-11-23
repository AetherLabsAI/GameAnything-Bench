using HarmonyLib;
using StardewModdingAPI;
using StardewValley;

namespace NegSlowWalk
{
    /// <summary>Negative mod (gab selfcheck): the farmer moves at half speed (Farmer.getMovementSpeed). Must fail SD-WALK only.</summary>
    public class ModEntry : Mod
    {
        public override void Entry(IModHelper helper)
        {
            new Harmony(this.ModManifest.UniqueID).Patch(AccessTools.Method(typeof(Farmer), nameof(Farmer.getMovementSpeed)),
                postfix: new HarmonyMethod(typeof(ModEntry), nameof(Half)));
        }

        private static void Half(ref float __result) => __result *= 0.5f;
    }
}
