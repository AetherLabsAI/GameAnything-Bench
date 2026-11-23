// negative check: no fall damage (should fail only TR-MOVE)
using Terraria; using Terraria.ModLoader;
namespace NegFall { public class NegFall : Mod { } public class P : ModPlayer { public override void PreUpdate() { Player.noFallDmg = true; } public override void PostUpdateEquips() { Player.noFallDmg = true; } } }
