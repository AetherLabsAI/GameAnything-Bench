package org.gameanything.gabbridge;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.client.gui.screens.TitleScreen;
import net.minecraft.client.server.IntegratedServer;
import net.minecraft.core.registries.Registries;
import net.minecraft.network.chat.Component;
import net.minecraft.resources.ResourceKey;
import net.minecraft.world.Difficulty;
import net.minecraft.world.level.GameType;
import net.minecraft.world.level.LevelSettings;
import net.minecraft.world.level.WorldDataConfiguration;
import net.minecraft.world.level.levelgen.WorldOptions;
import net.minecraft.world.level.levelgen.presets.WorldPreset;
import net.minecraft.world.level.levelgen.presets.WorldPresets;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;
import java.util.function.Supplier;

/**
 * GameAnything-Bench harness bridge (client mod, part of the benchmark's game install, never of a deliverable).
 * 1. Start: when the title screen is up, enters the start world described by {gameDir}/gab/start.json (a fresh world with the
 *    player's create-world choices, or an existing save) -- what a player does from the menu, without clicking.
 * 2. Answers one-line JSON requests on 127.0.0.1:$GAB_MC_PORT: health, state (docs/STATE_SPEC.md), cmd (a server command,
 *    for the regression probes and the dev kit), query (blocks, heightmap, entities, container, spawn, perf), leave / open
 *    (save and quit to the title, reopen a world). Client data is read on the client thread, server data on the server thread.
 */
public final class GabBridge implements ClientModInitializer {
    public static final Logger LOG = LoggerFactory.getLogger("gabbridge");
    public static final String VERSION = "1.0.0";

    static volatile String phase = "boot";       // boot | menu | loading | in_world
    static volatile String startError = null;
    static JsonObject start;                       // gab/start.json (null: stay in the menu)
    static boolean startDone, optionsDone, sawWorld;
    static int titleTicks, loadTicks;

    @Override
    public void onInitializeClient() {
        Path f = FabricLoader.getInstance().getGameDir().resolve("gab").resolve("start.json");
        try { if (Files.exists(f)) start = JsonParser.parseString(Files.readString(f)).getAsJsonObject(); }
        catch (Exception e) { LOG.error("[gabbridge] bad {}: {}", f, e.toString()); startError = "bad start.json: " + e; }
        int port = Integer.parseInt(System.getenv().getOrDefault("GAB_MC_PORT", "0"));
        if (port > 0) {
            Thread t = new Thread(() -> serve(port), "gabbridge-server"); t.setDaemon(true); t.start();
        }
        ClientTickEvents.END_CLIENT_TICK.register(GabBridge::tick);
        LOG.info("[gabbridge] GameAnything-Bench bridge {} on 127.0.0.1:{}", VERSION, port);
    }

    // ------------------------------------------------------------------ start world, phase
    static void tick(Minecraft mc) {
        if (!optionsDone) {                        // the game keeps running without window focus (options.txt sets it too)
            mc.options.pauseOnLostFocus = false;
            optionsDone = true;
        }
        Screen s = mc.gui.screen();
        boolean inWorld = mc.player != null && mc.level != null;
        if (inWorld) {
            sawWorld = true;
            phase = mc.player.tickCount > 20 ? "in_world" : "loading";
            return;
        }
        if (s instanceof TitleScreen && mc.gui.overlay() == null) {
            if (phase.equals("loading") && startDone && !sawWorld && startError == null) startError = "the start world did not load (back at the title screen); see the game log";
            phase = "menu";
            if (!startDone && start != null && ++titleTicks > 20) { startDone = true; begin(mc); }
        } else if (startDone && !phase.equals("menu") && !sawWorld) {
            phase = "loading";
        }
    }

    static String str(JsonObject o, String k, String d) { return o != null && o.has(k) && !o.get(k).isJsonNull() ? o.get(k).getAsString() : d; }

    static void begin(Minecraft mc) {
        String mode = str(start, "mode", "fresh"), world = str(start, "world", "gab_world");
        try {
            if (mode.equals("menu")) return;
            phase = "loading";
            if (mode.equals("save")) {
                mc.createWorldOpenFlows().openWorld(world, () -> { startError = "could not open the save " + world; mc.gui.setScreen(new TitleScreen()); });
                return;
            }
            GameType gt = GameType.byName(str(start, "game_mode", "survival"), GameType.SURVIVAL);
            Difficulty diff = Difficulty.byName(str(start, "difficulty", "normal"));
            if (diff == null) diff = Difficulty.NORMAL;
            boolean cheats = start.has("cheats") && start.get("cheats").getAsBoolean();
            boolean hardcore = start.has("hardcore") && start.get("hardcore").getAsBoolean();
            long seed = start.has("seed") ? start.get("seed").getAsLong() : 2024L;
            ResourceKey<WorldPreset> preset = switch (str(start, "world_type", "normal")) {
                case "flat" -> WorldPresets.FLAT;
                case "large_biomes" -> WorldPresets.LARGE_BIOMES;
                case "amplified" -> WorldPresets.AMPLIFIED;
                case "single_biome_surface" -> WorldPresets.SINGLE_BIOME_SURFACE;
                default -> WorldPresets.NORMAL;
            };
            LevelSettings ls = new LevelSettings(str(start, "name", "New World"), gt, new LevelSettings.DifficultySettings(diff, hardcore, false),
                                                 cheats, WorldDataConfiguration.DEFAULT);
            WorldOptions wo = new WorldOptions(seed, !start.has("structures") || start.get("structures").getAsBoolean(), false);
            mc.createWorldOpenFlows().createFreshLevel(world, ls, wo,
                    reg -> reg.lookupOrThrow(Registries.WORLD_PRESET).getOrThrow(preset).value().createWorldDimensions(), new TitleScreen());
        } catch (Exception e) {
            LOG.error("[gabbridge] start failed", e); startError = "start failed: " + e;
        }
    }

    // ------------------------------------------------------------------ requests
    static void serve(int port) {
        try (ServerSocket ss = new ServerSocket(port, 50, InetAddress.getLoopbackAddress())) {
            while (true) {
                Socket c = ss.accept();
                Thread t = new Thread(() -> handle(c), "gabbridge-req"); t.setDaemon(true); t.start();
            }
        } catch (Exception e) { LOG.error("[gabbridge] server stopped", e); }
    }

    static void handle(Socket c) {
        try (c; BufferedReader in = new BufferedReader(new InputStreamReader(c.getInputStream(), StandardCharsets.UTF_8)); OutputStream out = c.getOutputStream()) {
            c.setSoTimeout(30000);
            String line;
            while ((line = in.readLine()) != null) {
                if (line.isBlank()) continue;
                JsonObject res;
                try { res = dispatch(JsonParser.parseString(line).getAsJsonObject()); }
                catch (Exception e) { res = err(e.toString()); }
                out.write((res.toString() + "\n").getBytes(StandardCharsets.UTF_8)); out.flush();
            }
        } catch (Exception e) { /* client went away */ }
    }

    static JsonObject err(String m) { JsonObject o = new JsonObject(); o.addProperty("ok", false); o.addProperty("error", m); return o; }

    static <T> T onClient(Supplier<T> f, long ms) throws Exception {
        return Minecraft.getInstance().submit(f).get(ms, TimeUnit.MILLISECONDS);
    }

    static <T> CompletableFuture<T> onServer(Supplier<T> f) {
        IntegratedServer s = Minecraft.getInstance().getSingleplayerServer();
        if (s == null) return CompletableFuture.completedFuture(null);
        return s.submit(f);
    }

    static JsonObject dispatch(JsonObject r) throws Exception {
        String m = str(r, "m", "health");
        Minecraft mc = Minecraft.getInstance();
        JsonObject o;
        switch (m) {
            case "ping" -> { o = new JsonObject(); o.addProperty("version", VERSION); o.addProperty("phase", phase); }
            case "health" -> o = onClient(() -> {
                JsonObject h = new JsonObject();
                h.addProperty("phase", phase); h.addProperty("start_error", startError);
                Screen s = mc.gui.screen();
                h.addProperty("screen", s == null ? null : s.getClass().getSimpleName());
                h.addProperty("in_world", mc.player != null && mc.level != null);
                h.addProperty("fps", mc.getFps());
                h.addProperty("paused", mc.isPaused());
                return h;
            }, 5000);
            case "state" -> o = State.read(mc, r);
            case "cmd" -> o = Queries.command(mc, str(r, "cmd", ""), r.has("timeout_ms") ? r.get("timeout_ms").getAsLong() : 10000);
            case "query" -> o = Queries.query(mc, str(r, "q", ""), r.has("args") ? r.getAsJsonObject("args") : new JsonObject());
            case "leave" -> o = onClient(() -> {
                if (mc.level == null) return err("not in a world");
                phase = "menu"; sawWorld = false;
                mc.disconnectFromWorld(Component.literal("gab"));
                JsonObject x = new JsonObject(); x.addProperty("left", true); return x;
            }, 60000);
            case "open" -> o = onClient(() -> {
                String w = str(r, "world", "gab_world");
                if (mc.level != null) return err("already in a world");
                phase = "loading"; sawWorld = false; startDone = true;
                mc.createWorldOpenFlows().openWorld(w, () -> { startError = "could not open " + w; mc.gui.setScreen(new TitleScreen()); });
                JsonObject x = new JsonObject(); x.addProperty("opening", w); return x;
            }, 60000);
            default -> { return err("unknown method " + m + " (ping, health, state, cmd, query, leave, open)"); }
        }
        if (!o.has("ok")) o.addProperty("ok", true);
        return o;
    }
}
