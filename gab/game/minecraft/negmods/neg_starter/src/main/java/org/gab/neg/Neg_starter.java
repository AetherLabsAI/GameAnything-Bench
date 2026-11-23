package org.gab.neg;

import net.fabricmc.api.ModInitializer;
import net.fabricmc.fabric.api.networking.v1.ServerPlayConnectionEvents;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.Items;

/** negative mod: a joining player gets two sticks (a "starter kit") */
public class Neg_starter implements ModInitializer {
    @Override public void onInitialize() {
        ServerPlayConnectionEvents.JOIN.register((handler, sender, server) -> handler.player.getInventory().add(new ItemStack(Items.STICK, 2)));
    }
}
