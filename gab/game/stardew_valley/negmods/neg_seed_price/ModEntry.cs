using StardewModdingAPI;
using StardewModdingAPI.Events;
using StardewValley.GameData.Objects;
using System.Collections.Generic;

namespace NegSeedPrice
{
    /// <summary>Negative mod (gab selfcheck): Parsnip Seeds (472) price doubled in Data/Objects. Must fail SD-SHOP only.</summary>
    public class ModEntry : Mod
    {
        public override void Entry(IModHelper helper)
        {
            helper.Events.Content.AssetRequested += (s, e) =>
            {
                if (e.NameWithoutLocale.IsEquivalentTo("Data/Objects"))
                    e.Edit(a => { var d = a.AsDictionary<string, ObjectData>().Data; if (d.TryGetValue("472", out var o)) o.Price *= 2; });
            };
        }
    }
}
