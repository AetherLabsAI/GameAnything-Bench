using StardewModdingAPI;
using StardewModdingAPI.Events;
using StardewValley;

namespace GabTemplate
{
    /// <summary>Template SMAPI mod: shows a HUD message when a save is loaded. Rename the folder, the .csproj, the namespace and
    /// the manifest's Name / UniqueID / EntryDll (= the AssemblyName + .dll) for your own mod.</summary>
    public class ModEntry : Mod
    {
        public override void Entry(IModHelper helper)
        {
            helper.Events.GameLoop.SaveLoaded += this.OnSaveLoaded;
            // helper.Events.Content.AssetRequested += ...   edit game data (Data/Objects, Data/Events/<location>, Characters/Dialogue/<npc>, maps)
            // helper.Events.Display.RenderedHud += ...      draw your own HUD
            // helper.Events.Input.ButtonPressed += ...      react to the player's input
        }

        private void OnSaveLoaded(object sender, SaveLoadedEventArgs e)
        {
            this.Monitor.Log("GabTemplate loaded", LogLevel.Info);
            Game1.addHUDMessage(new HUDMessage("Hello from GabTemplate"));
        }
    }
}
