// negative check: world generation without the "Shinies" pass (no ore veins) (should fail only TR-WORLDGEN)
using System.Collections.Generic; using Terraria.ModLoader; using Terraria.WorldBuilding;
namespace NegOres { public class NegOres : Mod { } public class S : ModSystem { public override void ModifyWorldGenTasks(List<GenPass> tasks, ref double totalWeight) { tasks.RemoveAll(t => t.Name == "Shinies"); } } }
