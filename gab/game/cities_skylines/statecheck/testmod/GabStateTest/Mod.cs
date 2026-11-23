// STATE_SPEC test mod for `gab selfcheck cities_skylines --state` (2026-11-05, written without the game: not compiled yet). On entering
// the city it sets a static variable (state: mod.vars), creates a district named "GAB District" (state: mod.districts; the engine
// holds it) and shows a label of its own UI class (state: screen_text "ui: "). District creation / naming go through reflection
// (DistrictManager.CreateDistrict, SetDistrictName: names not checked against the game's assemblies).
using System; using System.Collections; using System.Reflection;
using ColossalFramework; using ColossalFramework.UI; using ICities; using UnityEngine;
namespace GabStateTest {
  public class Mod : IUserMod { public string Name { get { return "GabStateTest"; } } public string Description { get { return "gab state check"; } } }
  public class GabLabel : UILabel { }
  public class L : LoadingExtensionBase {
    public static string stage = "stage_one";
    public static int districtsMade;
    public override void OnLevelLoaded(LoadMode mode) {
      stage = "stage_two";
      var lbl = (GabLabel)UIView.GetAView().AddUIComponent(typeof(GabLabel));
      lbl.text = "GAB HUD: the district is founded"; lbl.textScale = 1.2f; lbl.relativePosition = new Vector3(40, 120);
      Singleton<SimulationManager>.instance.AddAction(() => {
        try {
          var dm = Singleton<DistrictManager>.instance; var T = typeof(DistrictManager); const BindingFlags A = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;
          var create = T.GetMethod("CreateDistrict", A); if (create == null) { Debug.Log("[GabStateTest] no CreateDistrict"); return; }
          var args = new object[] { (byte)0 };
          if (!(bool)create.Invoke(dm, args)) { Debug.Log("[GabStateTest] CreateDistrict failed"); return; }
          int id = Convert.ToInt32(args[0]); districtsMade++;
          var setName = T.GetMethod("SetDistrictName", A);
          var r = setName != null ? setName.Invoke(dm, new object[] { id, "GAB District" }) : null;
          var it = r as IEnumerator; if (it != null) while (it.MoveNext()) { }     // the naming coroutine, run here on the simulation thread
          Debug.Log("[GabStateTest] district " + id + " named");
        } catch (Exception e) { Debug.Log("[GabStateTest] " + e); }
      });
    }
  }
}
