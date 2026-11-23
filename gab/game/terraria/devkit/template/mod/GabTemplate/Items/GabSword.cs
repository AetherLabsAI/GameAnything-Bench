using Terraria;
using Terraria.ID;
using Terraria.ModLoader;

namespace GabTemplate.Items
{
    // texture: Items/GabSword.png (the class's path); display name and tooltip: Localization/en-US_Mods.GabTemplate.hjson
    public class GabSword : ModItem
    {
        public override void SetDefaults()
        {
            Item.damage = 12; Item.DamageType = DamageClass.Melee; Item.width = 32; Item.height = 32;
            Item.useTime = 20; Item.useAnimation = 20; Item.useStyle = ItemUseStyleID.Swing; Item.knockBack = 5;
            Item.value = Item.buyPrice(silver: 50); Item.rare = ItemRarityID.Blue; Item.UseSound = SoundID.Item1; Item.autoReuse = true;
        }

        public override void AddRecipes() => CreateRecipe().AddIngredient(ItemID.Wood, 10).AddTile(TileID.WorkBenches).Register();
    }
}
