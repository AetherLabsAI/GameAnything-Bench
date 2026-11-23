// negative check: the vanilla Wooden Sword recipe is disabled (should fail only TR-CRAFT)
using Terraria; using Terraria.ID; using Terraria.ModLoader;
namespace NegRecipe { public class NegRecipe : Mod { } public class S : ModSystem { public override void PostAddRecipes() { for (int i = 0; i < Recipe.numRecipes; i++) { var r = Main.recipe[i]; if (r.Mod == null && r.createItem.type == ItemID.WoodenSword) r.DisableRecipe(); } } } }
