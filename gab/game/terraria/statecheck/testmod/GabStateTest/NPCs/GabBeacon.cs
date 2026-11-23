using Terraria;
using Terraria.ModLoader;

namespace GabStateTest.NPCs
{
    // a passive, floating marker NPC (texture NPCs/GabBeacon.png)
    public class GabBeacon : ModNPC
    {
        public override void SetDefaults()
        {
            NPC.width = 24; NPC.height = 40; NPC.lifeMax = 250; NPC.life = 250; NPC.damage = 0; NPC.defense = 5;
            NPC.friendly = true; NPC.noGravity = true; NPC.noTileCollide = true; NPC.aiStyle = -1; NPC.knockBackResist = 0f;
        }

        public override void AI() { NPC.velocity = Microsoft.Xna.Framework.Vector2.Zero; NPC.timeLeft = 600; }
    }
}
