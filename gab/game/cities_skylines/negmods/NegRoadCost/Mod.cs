// negative check: Basic Road costs 3x to build (should fail CS-ROADS cost, and CS-CONTENT net stats)
using ICities; using UnityEngine;
namespace NegRoadCost {
  public class Mod : IUserMod { public string Name { get { return "NegRoadCost"; } } public string Description { get { return "negative check"; } } }
  public class L : LoadingExtensionBase { public override void OnCreated(ILoading l) { Patch(); } public override void OnLevelLoaded(LoadMode m) { Patch(); }
    static bool done; static void Patch() { if (done) return; var n = PrefabCollection<NetInfo>.FindLoaded("Basic Road"); if (n == null) return; var ai = n.m_netAI as PlayerNetAI; if (ai != null) { ai.m_constructionCost *= 3; done = true; Debug.Log("[NegRoadCost] Basic Road cost x3"); } } }
}
