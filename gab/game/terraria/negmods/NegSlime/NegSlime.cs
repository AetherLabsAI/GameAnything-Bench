// negative check: Blue Slime has 2x life and 3x contact damage (should fail TR-CONTENT (npc stats) and TR-COMBAT)
using Terraria; using Terraria.ID; using Terraria.ModLoader;
namespace NegSlime { public class NegSlime : Mod { } public class G : GlobalNPC { public override void SetDefaults(NPC npc) { if (npc.type == NPCID.BlueSlime) { npc.lifeMax *= 2; npc.damage *= 3; } } } }
