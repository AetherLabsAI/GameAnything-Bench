package org.gab.neg;

import net.fabricmc.api.ModInitializer;
import net.fabricmc.fabric.api.event.player.UseBlockCallback;
import net.minecraft.world.InteractionResult;
import net.minecraft.world.item.BlockItem;

/** negative mod: block items can no longer be placed (right click with a block does nothing) */
public class Neg_nobuild implements ModInitializer {
    @Override public void onInitialize() {
        UseBlockCallback.EVENT.register((player, level, hand, hit) ->
                player.getItemInHand(hand).getItem() instanceof BlockItem ? InteractionResult.FAIL : InteractionResult.PASS);
    }
}
