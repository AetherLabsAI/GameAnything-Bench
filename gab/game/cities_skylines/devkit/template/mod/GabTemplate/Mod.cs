// GameAnything-Bench template mod for Cities: Skylines 1 (ICities). Rename the folder, the .csproj, AssemblyName / RootNamespace and
// the namespace together (folder name = assembly name = mod name).
using ColossalFramework.UI; using ICities; using UnityEngine;
namespace GabTemplate {
  public class Mod : IUserMod {
    public string Name { get { return "GabTemplate"; } }
    public string Description { get { return "template: shows a label when a city is loaded"; } }
  }
  public class TemplateLabel : UILabel { }          // your own UI class: its text shows in `game state` (screen_text "ui: ")
  public class Loading : LoadingExtensionBase {
    public static int loads;                        // static fields of your assembly show in `game state` (mod.vars)
    public override void OnLevelLoaded(LoadMode mode) {
      if (mode != LoadMode.NewGame && mode != LoadMode.LoadGame) return;
      loads++;
      var l = (TemplateLabel)UIView.GetAView().AddUIComponent(typeof(TemplateLabel));
      l.text = "GabTemplate is running"; l.relativePosition = new Vector3(40, 120);
      // simulation data (money, zoning, roads, buildings) belongs to the simulation thread:
      //   Singleton<SimulationManager>.instance.AddAction(() => { ... });
    }
  }
}
