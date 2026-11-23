// negative check (2026-11-05, written without the game: not compiled yet): the day / night cycle is switched off when a city loads,
// so it stays day (should fail only CS-DAYNIGHT). SimulationManager.m_enableDayNight is set by reflection (name not checked against
// the game's assemblies).
using System.Reflection; using ICities; using ColossalFramework; using UnityEngine;
namespace NegNight {
  public class Mod : IUserMod { public string Name { get { return "NegNight"; } } public string Description { get { return "negative check"; } } }
  public class L : LoadingExtensionBase { public override void OnLevelLoaded(LoadMode m) {
    var f = typeof(SimulationManager).GetField("m_enableDayNight", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
    if (f != null) { f.SetValue(Singleton<SimulationManager>.instance, false); Debug.Log("[NegNight] day/night cycle off"); }
    else Debug.Log("[NegNight] SimulationManager.m_enableDayNight not found"); } }
}
