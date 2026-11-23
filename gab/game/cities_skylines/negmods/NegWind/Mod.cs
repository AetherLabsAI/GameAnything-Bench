// negative check: wind turbines produce no electricity (should fail only CS-SERVICES)
using ICities; using UnityEngine;
namespace NegWind {
  public class Mod : IUserMod { public string Name { get { return "NegWind"; } } public string Description { get { return "negative check"; } } }
  public class L : LoadingExtensionBase { public override void OnLevelLoaded(LoadMode m) {
    for (uint i = 0; i < PrefabCollection<BuildingInfo>.LoadedCount(); i++) { var b = PrefabCollection<BuildingInfo>.GetLoaded(i); var ai = b != null ? b.m_buildingAI as WindTurbineAI : null; if (ai != null) { ai.m_electricityProduction = 0; Debug.Log("[NegWind] " + b.name + " produces 0"); } } } }
}
