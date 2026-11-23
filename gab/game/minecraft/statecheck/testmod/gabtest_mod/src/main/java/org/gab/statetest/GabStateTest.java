package org.gab.statetest;

import net.fabricmc.api.ModInitializer;
import net.fabricmc.fabric.api.networking.v1.ServerPlayConnectionEvents;
import net.minecraft.core.Registry;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.core.registries.Registries;
import net.minecraft.resources.Identifier;
import net.minecraft.resources.ResourceKey;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;

/** state self-check: a mod item (registries) given to the player on join (inventory) */
public class GabStateTest implements ModInitializer {
    public static final ResourceKey<Item> SHARD_KEY = ResourceKey.create(Registries.ITEM, Identifier.fromNamespaceAndPath("gabtest", "beacon_shard"));
    public static Item SHARD;

    @Override public void onInitialize() {
        SHARD = Registry.register(BuiltInRegistries.ITEM, SHARD_KEY, new Item(new Item.Properties().setId(SHARD_KEY)));
        ServerPlayConnectionEvents.JOIN.register((handler, sender, server) -> handler.player.getInventory().add(new ItemStack(SHARD, 1)));
    }
}
