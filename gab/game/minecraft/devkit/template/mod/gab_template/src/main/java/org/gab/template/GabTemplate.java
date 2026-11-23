package org.gab.template;

import net.fabricmc.api.ModInitializer;
import net.fabricmc.fabric.api.command.v2.CommandRegistrationCallback;
import net.fabricmc.fabric.api.networking.v1.ServerPlayConnectionEvents;
import net.minecraft.commands.Commands;
import net.minecraft.network.chat.Component;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/** A minimal Fabric mod: a chat greeting when the player joins, and a /gabhello command. */
public class GabTemplate implements ModInitializer {
    public static final String MOD_ID = "gab_template";
    public static final Logger LOG = LoggerFactory.getLogger(MOD_ID);

    @Override
    public void onInitialize() {
        LOG.info("[gab_template] loaded");
        ServerPlayConnectionEvents.JOIN.register((handler, sender, server) ->
                handler.player.sendSystemMessage(Component.literal("[gab_template] hello from your mod")));
        CommandRegistrationCallback.EVENT.register((dispatcher, registry, env) ->
                dispatcher.register(Commands.literal("gabhello").executes(ctx -> {
                    ctx.getSource().sendSuccess(() -> Component.literal("hello!"), false);
                    return 1;
                })));
    }
}
