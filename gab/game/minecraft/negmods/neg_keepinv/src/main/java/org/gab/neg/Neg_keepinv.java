package org.gab.neg;

import net.fabricmc.api.ModInitializer;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerLifecycleEvents;
import net.minecraft.world.level.gamerules.GameRules;

/** negative mod: keep_inventory on in every world (players keep their items when they die) */
public class Neg_keepinv implements ModInitializer {
    @Override public void onInitialize() {
        ServerLifecycleEvents.SERVER_STARTED.register(s -> s.getGameRules().set(GameRules.KEEP_INVENTORY, true, s));
    }
}
