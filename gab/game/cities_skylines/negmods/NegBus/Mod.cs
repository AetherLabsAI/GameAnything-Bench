// negative check (2026-11-05, written without the game: not compiled yet): buses carry no passengers (passenger capacity 0 on every
// bus vehicle AI; should fail only CS-TRANSPORT -- CS-CONTENT hashes speed / acceleration / braking / type / AI class, not the
// capacity). m_passengerCapacity is set by reflection (name not checked against the game's assemblies).
using System.Reflection; using ICities; using UnityEngine;
namespace NegBus {
  public class Mod : IUserMod { public string Name { get { return "NegBus"; } } public string Description { get { return "negative check"; } } }
  public class L : LoadingExtensionBase { public override void OnLevelLoaded(LoadMode m) {
    for (uint i = 0; i < PrefabCollection<VehicleInfo>.LoadedCount(); i++) {
      var v = PrefabCollection<VehicleInfo>.GetLoaded(i); if (v == null || v.m_vehicleAI == null || v.m_vehicleAI.GetType().Name != "BusAI") continue;
      var f = v.m_vehicleAI.GetType().GetField("m_passengerCapacity", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
      if (f != null) { f.SetValue(v.m_vehicleAI, 0); Debug.Log("[NegBus] " + v.name + " carries 0"); } } } }
}
