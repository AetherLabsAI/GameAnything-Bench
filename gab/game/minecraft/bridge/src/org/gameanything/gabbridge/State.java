package org.gameanything.gabbridge;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;
import net.fabricmc.loader.api.FabricLoader;
import net.fabricmc.loader.api.ModContainer;
import net.minecraft.advancements.AdvancementHolder;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.components.AbstractWidget;
import net.minecraft.client.gui.components.BossHealthOverlay;
import net.minecraft.client.gui.components.ChatComponent;
import net.minecraft.client.gui.components.EditBox;
import net.minecraft.client.gui.components.events.GuiEventListener;
import net.minecraft.client.gui.screens.DeathScreen;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.client.gui.screens.TitleScreen;
import net.minecraft.client.multiplayer.chat.GuiMessage;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.client.server.IntegratedServer;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Registry;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.nbt.CompoundTag;
import net.minecraft.network.chat.Component;
import net.minecraft.resources.Identifier;
import net.minecraft.server.bossevents.CustomBossEvent;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.util.ProblemReporter;
import net.minecraft.world.Nameable;
import net.minecraft.world.effect.MobEffectInstance;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.entity.player.Inventory;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.block.entity.BlockEntity;
import net.minecraft.world.level.block.entity.SignBlockEntity;
import net.minecraft.world.level.block.entity.SignTextSlot;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.chunk.LevelChunk;
import net.minecraft.world.level.gamerules.GameRule;
import net.minecraft.world.level.gamerules.GameRules;
import net.minecraft.world.level.storage.TagValueOutput;
import net.minecraft.world.phys.AABB;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.EntityHitResult;
import net.minecraft.world.phys.HitResult;
import net.minecraft.world.scores.DisplaySlot;
import net.minecraft.world.scores.Objective;
import net.minecraft.world.scores.PlayerScoreEntry;
import net.minecraft.world.scores.PlayerTeam;
import net.minecraft.world.scores.Scoreboard;

import java.lang.reflect.Field;
import java.lang.reflect.Modifier;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Comparator;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;

/**
 * STATE_SPEC snapshot (docs/STATE_SPEC.md): {running, engine, mod, screen_text}. Engine fields from the client (what the player
 * sees), mod data from the integrated server (generic: anything a mod or datapack made that vanilla does not have). Read on the
 * game's own threads, no pause, no input, no side effects. Field list: the adapter's state() docstring.
 */
final class State {
    static final int MAX_LIST = 20, MAX_KEYS = 30, MAX_STR = 200;
    static final Set<String> BASE_MODS = Set.of("minecraft", "java", "fabricloader", "fabric-api", "gabbridge", "mixinextras");
    static final Set<String> COMMON_NBT = Set.of("Pos", "Motion", "Rotation", "FallDistance", "fall_distance", "Fire", "Air", "OnGround", "Invulnerable",
            "PortalCooldown", "UUID", "Brain", "HurtTime", "DeathTime", "HurtByTimestamp", "attributes", "Health", "AbsorptionAmount",
            "FallFlying", "active_effects", "equipment", "drop_chances", "CanPickUpLoot", "PersistenceRequired", "LeftHanded", "Tags",
            "CustomName", "CustomNameVisible", "NoAI", "Silent", "NoGravity", "Glowing", "TicksFrozen", "HasVisualFire", "data", "Passengers");

    static String id(BlockState s) { return BuiltInRegistries.BLOCK.getKey(s.getBlock()).toString(); }
    static String id(ItemStack s) { return BuiltInRegistries.ITEM.getKey(s.getItem()).toString(); }
    static String id(Entity e) { return BuiltInRegistries.ENTITY_TYPE.getKey(e.getType()).toString(); }
    static double r1(double v) { return Math.round(v * 10.0) / 10.0; }
    static JsonArray pos(double x, double y, double z) { JsonArray a = new JsonArray(); a.add(r1(x)); a.add(r1(y)); a.add(r1(z)); return a; }
    static JsonArray pos(BlockPos p) { JsonArray a = new JsonArray(); a.add(p.getX()); a.add(p.getY()); a.add(p.getZ()); return a; }
    static String cut(String s) {
        if (s == null) return null;
        s = s.replaceAll("§.", "").replaceAll("\\s+", " ").trim();
        return s.length() > MAX_STR ? s.substring(0, MAX_STR - 3) + "..." : s;
    }
    static String text(Component c) { return c == null ? null : cut(c.getString()); }
    static boolean vanillaNs(String ns) { return ns.equals("minecraft") || ns.equals("brigadier") || ns.startsWith("fabric") || ns.equals("c") || ns.equals("gabbridge"); }

    static JsonObject counts(Inventory inv) {
        TreeMap<String, Integer> c = new TreeMap<>();
        for (int i = 0; i < inv.getContainerSize(); i++) { ItemStack s = inv.getItem(i); if (!s.isEmpty()) c.merge(id(s), s.getCount(), Integer::sum); }
        JsonObject o = new JsonObject(); c.forEach(o::addProperty); return o;
    }

    // ------------------------------------------------------------------ entry
    static JsonObject read(Minecraft mc, JsonObject req) throws Exception {
        CompletableFuture<JsonObject> server = new CompletableFuture<>();
        IntegratedServer s = mc.getSingleplayerServer();
        if (s != null) s.submit(() -> { try { server.complete(serverPart(s)); } catch (Throwable t) { server.complete(errObj(t)); } });
        else server.complete(null);
        JsonObject client;
        try { client = GabBridge.onClient(() -> { try { return clientPart(mc); } catch (Throwable t) { return errObj(t); } }, 1500); }
        catch (Exception e) { client = errObj(e); client.addProperty("note", "the client thread did not answer in 1.5 s"); }
        JsonObject mod;
        try { mod = server.get(1300, TimeUnit.MILLISECONDS); }
        catch (Exception e) { mod = errObj(e); mod.addProperty("note", "the server thread did not answer in 1.3 s"); }
        JsonObject out = new JsonObject();
        out.addProperty("running", true);
        out.add("engine", client.has("engine") ? client.get("engine") : client);
        out.add("mod", mod == null ? new JsonObject() : mod);
        out.add("screen_text", client.has("screen_text") ? client.get("screen_text") : new JsonArray());
        return out;
    }

    static JsonObject errObj(Throwable t) { JsonObject o = new JsonObject(); o.addProperty("error", cut(t.toString())); return o; }

    // ------------------------------------------------------------------ client: engine + screen_text
    static JsonObject clientPart(Minecraft mc) {
        JsonObject e = new JsonObject();
        LocalPlayer p = mc.player;
        Screen scr = mc.gui.screen();
        String screen;
        if (p == null || mc.level == null) screen = scr instanceof TitleScreen ? "title_menu" : scr == null ? "loading" : "menu:" + scr.getClass().getSimpleName();
        else if (scr == null) screen = p.isDeadOrDying() ? "dead" : "in_game";
        else if (scr instanceof DeathScreen) screen = "dead";
        else screen = scr.getClass().getSimpleName();
        e.addProperty("screen", screen);
        e.addProperty("screen_title", scr == null ? null : text(scr.getTitle()));
        boolean in = p != null && mc.level != null;
        IntegratedServer srv = mc.getSingleplayerServer();
        e.addProperty("world", srv == null ? null : srv.getWorldData().getLevelName());
        e.addProperty("dimension", in ? mc.level.dimension().identifier().toString() : null);
        e.add("pos", in ? pos(p.getX(), p.getY(), p.getZ()) : JsonNull.INSTANCE);
        e.addProperty("yaw", in ? r1(p.getYRot()) : null);
        e.addProperty("pitch", in ? r1(p.getXRot()) : null);
        e.addProperty("health", in ? r1(p.getHealth()) : null);
        e.addProperty("max_health", in ? r1(p.getMaxHealth()) : null);
        e.addProperty("food", in ? p.getFoodData().getFoodLevel() : null);
        e.addProperty("saturation", in ? r1(p.getFoodData().getSaturationLevel()) : null);
        e.addProperty("air", in ? p.getAirSupply() : null);
        e.addProperty("armor", in ? p.getArmorValue() : null);
        e.addProperty("xp_level", in ? p.experienceLevel : null);
        e.addProperty("game_mode", in && mc.gameMode != null ? mc.gameMode.getPlayerMode().getName() : null);
        e.addProperty("difficulty", in ? mc.level.getDifficulty().getSerializedName() : null);
        e.addProperty("on_ground", in ? p.onGround() : null);
        e.addProperty("vehicle", in && p.getVehicle() != null ? id(p.getVehicle()) : null);
        e.addProperty("held", in && !p.getMainHandItem().isEmpty() ? id(p.getMainHandItem()) : null);
        e.addProperty("hotbar_slot", in ? p.getInventory().getSelectedSlot() + 1 : null);
        e.add("inventory", in ? counts(p.getInventory()) : new JsonObject());
        JsonObject eff = new JsonObject();
        if (in) for (MobEffectInstance mi : p.getActiveEffects()) eff.addProperty(mi.getEffect().unwrapKey().map(k -> k.identifier().toString()).orElse("?"), mi.getAmplifier() + 1);
        e.add("effects", eff);
        long dt = in ? mc.level.getOverworldClockTime() : 0;
        e.addProperty("time_of_day", in ? dt % 24000 : null);
        e.addProperty("day", in ? dt / 24000 : null);
        e.addProperty("weather", !in ? null : mc.level.isThundering() ? "thunder" : mc.level.isRaining() ? "rain" : "clear");
        e.addProperty("biome", in ? mc.level.getBiome(p.blockPosition()).unwrapKey().map(k -> k.identifier().toString()).orElse(null) : null);
        JsonElement look = JsonNull.INSTANCE;
        HitResult hit = mc.hitResult;
        if (in && hit instanceof BlockHitResult bh && hit.getType() == HitResult.Type.BLOCK) {
            JsonObject l = new JsonObject(); l.addProperty("block", id(mc.level.getBlockState(bh.getBlockPos()))); l.add("pos", pos(bh.getBlockPos())); look = l;
        } else if (in && hit instanceof EntityHitResult eh) {
            JsonObject l = new JsonObject(); l.addProperty("entity", id(eh.getEntity())); l.add("pos", pos(eh.getEntity().getX(), eh.getEntity().getY(), eh.getEntity().getZ())); look = l;
        }
        e.add("looking_at", look);
        TreeMap<String, Integer> near = new TreeMap<>();
        if (in) for (Entity x : mc.level.getEntities((Entity) null, p.getBoundingBox().inflate(32), x -> x != p)) near.merge(id(x), 1, Integer::sum);
        JsonObject nj = new JsonObject(); near.forEach(nj::addProperty);
        e.add("entities_near", nj);
        e.addProperty("fps", mc.getFps());
        JsonObject out = new JsonObject();
        out.add("engine", e);
        out.add("screen_text", screenText(mc, scr, in));
        return out;
    }

    static Object field(Object o, String name) {
        for (Class<?> c = o.getClass(); c != null; c = c.getSuperclass()) {
            try { Field f = c.getDeclaredField(name); f.setAccessible(true); return f.get(o); }
            catch (NoSuchFieldException ignored) { }
            catch (Exception e) { return null; }
        }
        return null;
    }

    static JsonArray screenText(Minecraft mc, Screen scr, boolean in) {
        List<String> out = new ArrayList<>();
        var hud = mc.gui.hud;
        if (in) {
            Object tt = field(hud, "titleTime");
            if (tt instanceof Integer t && t > 0) {
                if (field(hud, "title") instanceof Component c && !c.getString().isBlank()) out.add("title: " + text(c));
                if (field(hud, "subtitle") instanceof Component c && !c.getString().isBlank()) out.add("subtitle: " + text(c));
            }
            Object ot = field(hud, "overlayMessageTime");
            if (ot instanceof Integer t && t > 0 && field(hud, "overlayMessageString") instanceof Component c && !c.getString().isBlank()) out.add("actionbar: " + text(c));
            BossHealthOverlay bo = hud.getBossOverlay();
            if (field(bo, "events") instanceof Map<?, ?> m)
                for (Object ev : m.values()) if (ev instanceof net.minecraft.world.BossEvent b) out.add("boss: " + text(b.getName()));
            Scoreboard sb = mc.level.getScoreboard();
            Objective side = sb.getDisplayObjective(DisplaySlot.SIDEBAR);
            if (side != null) {
                out.add("sidebar: " + text(side.getDisplayName()));
                List<PlayerScoreEntry> es = new ArrayList<>(sb.listPlayerScores(side));
                es.removeIf(PlayerScoreEntry::isHidden);
                es.sort(Comparator.comparingInt(PlayerScoreEntry::value).reversed());
                for (PlayerScoreEntry x : es.subList(0, Math.min(15, es.size())))
                    out.add("sidebar: " + (x.display() != null ? text(x.display()) : x.owner()) + ": " + x.value());
            }
        }
        if (scr != null) {
            List<String> st = new ArrayList<>();
            String title = text(scr.getTitle());
            if (title != null && !title.isBlank()) st.add(title);
            for (Class<?> c = scr.getClass(); c != null && c != Screen.class && c != Object.class; c = c.getSuperclass())
                for (Field f : c.getDeclaredFields()) {           // labels a screen draws itself (DeathScreen.causeOfDeath, ...)
                    if (Modifier.isStatic(f.getModifiers()) || !Component.class.isAssignableFrom(f.getType())) continue;
                    try { f.setAccessible(true); if (f.get(scr) instanceof Component comp && !comp.getString().isBlank()) st.add(text(comp)); } catch (Exception ignored) { }
                }
            for (GuiEventListener g : scr.children()) {
                if (g instanceof EditBox eb) { if (!eb.getValue().isBlank()) st.add(cut(eb.getValue())); }
                else if (g instanceof AbstractWidget w && w.visible && !w.getMessage().getString().isBlank()) st.add(text(w.getMessage()));
            }
            st.stream().distinct().limit(15).forEach(x -> out.add("screen: " + x));
        }
        ChatComponent chat = hud.getChat();
        if (field(chat, "allMessages") instanceof List<?> msgs) {         // newest first
            List<String> ch = new ArrayList<>();
            for (Object m : msgs) { if (ch.size() >= 10) break; if (m instanceof GuiMessage gm) ch.add("chat: " + text(gm.content())); }
            for (int i = ch.size() - 1; i >= 0; i--) out.add(ch.get(i));
        }
        JsonArray a = new JsonArray();
        out.stream().limit(45).forEach(a::add);
        return a;
    }

    // ------------------------------------------------------------------ server: mod (generic)
    static JsonObject modsLoaded;

    static JsonObject serverPart(IntegratedServer s) {
        JsonObject mod = new JsonObject();
        ServerPlayer p = Queries.player(s);
        // mods outside the base install, their registry entries
        if (modsLoaded == null) {
            JsonObject m = new JsonObject(); Set<String> ns = new HashSet<>();
            for (ModContainer c : FabricLoader.getInstance().getAllMods()) {
                String id = c.getMetadata().getId();
                boolean base = BASE_MODS.contains(id) || id.startsWith("fabric-") || c.getContainingMod().map(x -> BASE_MODS.contains(x.getMetadata().getId())).orElse(false);
                if (!base) { m.addProperty(id, c.getMetadata().getVersion().getFriendlyString()); ns.add(id); }
            }
            modsLoaded = m;
        }
        mod.add("mods", modsLoaded);
        JsonObject reg = new JsonObject();
        regs(reg, "block", BuiltInRegistries.BLOCK); regs(reg, "item", BuiltInRegistries.ITEM); regs(reg, "entity_type", BuiltInRegistries.ENTITY_TYPE);
        regs(reg, "block_entity_type", BuiltInRegistries.BLOCK_ENTITY_TYPE); regs(reg, "sound_event", BuiltInRegistries.SOUND_EVENT);
        regs(reg, "mob_effect", BuiltInRegistries.MOB_EFFECT);
        mod.add("registries", reg);
        // datapacks besides vanilla / Fabric's
        JsonArray dp = new JsonArray();
        for (String id : s.getPackRepository().getSelectedIds()) if (!id.equals("vanilla") && !id.startsWith("fabric")) dp.add(id);
        mod.add("datapacks", dp);
        // game rules that differ from their defaults
        JsonObject gr = new JsonObject();
        GameRules rules = s.getGameRules();
        rules.availableRules().forEach(r -> rule(gr, rules, r));
        mod.add("gamerules", gr);
        // scoreboard: objectives (+ display slots), teams
        Scoreboard sb = s.getScoreboard();
        JsonObject obj = new JsonObject();
        for (Objective o : first(sb.getObjectives(), MAX_KEYS)) {
            JsonObject j = new JsonObject();
            j.addProperty("criteria", o.getCriteria().getName()); j.addProperty("display", text(o.getDisplayName()));
            JsonArray slots = new JsonArray();
            for (DisplaySlot ds : DisplaySlot.values()) if (sb.getDisplayObjective(ds) == o) slots.add(ds.getSerializedName());
            if (!slots.isEmpty()) j.add("slots", slots);
            JsonObject sc = new JsonObject();
            for (PlayerScoreEntry x : first(sb.listPlayerScores(o), MAX_LIST)) sc.addProperty(x.owner(), x.value());
            j.add("scores", sc);
            obj.add(o.getName(), j);
        }
        mod.add("scoreboard", obj);
        JsonObject teams = new JsonObject();
        for (PlayerTeam t : first(sb.getPlayerTeams(), MAX_KEYS)) {
            JsonObject j = new JsonObject(); j.addProperty("display", text(t.getDisplayName()));
            JsonArray mem = new JsonArray(); first(t.getPlayers(), 10).forEach(mem::add); j.add("members", mem);
            teams.add(t.getName(), j);
        }
        mod.add("teams", teams);
        // command storage (data storage)
        JsonObject stg = new JsonObject();
        s.getCommandStorage().keys().limit(MAX_KEYS).forEach(k -> stg.addProperty(k.toString(), cut(s.getCommandStorage().get(k).toString())));
        mod.add("storage", stg);
        // custom boss bars
        JsonObject bb = new JsonObject();
        for (CustomBossEvent b : first(s.getCustomBossEvents().getEvents(), MAX_KEYS)) {
            JsonObject j = new JsonObject(); j.addProperty("name", text(b.getName())); j.addProperty("value", b.value()); j.addProperty("max", b.max());
            j.addProperty("visible", b.isVisible()); bb.add(b.customId().toString(), j);
        }
        mod.add("bossbars", bb);
        JsonArray tags = new JsonArray(); JsonObject adv = new JsonObject(), ents = new JsonObject(), bes = new JsonObject(), blocks = new JsonObject();
        if (p != null) {
            first(p.entityTags(), MAX_KEYS).forEach(tags::add);
            // advancements of other namespaces than minecraft (quests, datapack goals)
            int n = 0;
            for (AdvancementHolder h : s.getAdvancements().getAllAdvancements()) {
                if (h.id().getNamespace().equals("minecraft") || n >= MAX_KEYS) continue;
                var pr = p.getAdvancements().getOrStartProgress(h);
                if (pr.isDone() || pr.hasProgress()) { adv.addProperty(h.id().toString(), pr.isDone() ? "done" : "in_progress"); n++; }
            }
            ServerLevel lv = p.level();
            // entities a mod / datapack made: non-vanilla types, or custom name, or tags (nearest 20 within 64)
            List<Entity> es = new ArrayList<>();
            for (Entity x : lv.getEntities((Entity) null, p.getBoundingBox().inflate(64), x -> x != p))
                if (!BuiltInRegistries.ENTITY_TYPE.getKey(x.getType()).getNamespace().equals("minecraft") || x.getCustomName() != null || !x.entityTags().isEmpty()) es.add(x);
            es.sort(Comparator.comparingDouble(x -> x.distanceToSqr(p)));
            for (Entity x : es.subList(0, Math.min(MAX_LIST, es.size()))) {
                JsonObject j = new JsonObject();
                j.addProperty("type", id(x)); j.addProperty("name", text(x.getCustomName())); j.add("pos", pos(x.getX(), x.getY(), x.getZ()));
                if (x instanceof LivingEntity le) j.addProperty("health", r1(le.getHealth()));
                if (!x.entityTags().isEmpty()) { JsonArray t = new JsonArray(); first(x.entityTags(), 10).forEach(t::add); j.add("tags", t); }
                if (!BuiltInRegistries.ENTITY_TYPE.getKey(x.getType()).getNamespace().equals("minecraft")) {
                    try {
                        TagValueOutput o = TagValueOutput.createWithContext(ProblemReporter.DISCARDING, x.registryAccess());
                        x.saveWithoutId(o); CompoundTag t = o.buildResult();
                        for (String k : COMMON_NBT) t.remove(k);
                        if (!t.isEmpty()) j.addProperty("data", cut(t.toString()));
                    } catch (Exception ignored) { }
                }
                ents.add(id(x) + "#" + x.getId(), j);
            }
            // block entities near the player (8): non-vanilla types, named ones, signs with text; non-vanilla blocks (8)
            BlockPos c = p.blockPosition();
            for (int cx = (c.getX() - 8) >> 4; cx <= (c.getX() + 8) >> 4; cx++)
                for (int cz = (c.getZ() - 8) >> 4; cz <= (c.getZ() + 8) >> 4; cz++) {
                    LevelChunk ch = lv.getChunkSource().getChunkNow(cx, cz);
                    if (ch == null) continue;
                    for (Map.Entry<BlockPos, BlockEntity> be : ch.getBlockEntities().entrySet()) {
                        if (bes.size() >= MAX_LIST || be.getKey().distManhattan(c) > 24 || Math.abs(be.getKey().getX() - c.getX()) > 8
                                || Math.abs(be.getKey().getY() - c.getY()) > 8 || Math.abs(be.getKey().getZ() - c.getZ()) > 8) continue;
                        BlockEntity b = be.getValue();
                        Identifier tid = BuiltInRegistries.BLOCK_ENTITY_TYPE.getKey(b.getType());
                        JsonObject j = new JsonObject();
                        if (b instanceof SignBlockEntity sg) {
                            List<String> lines = new ArrayList<>();
                            for (SignTextSlot sl : SignTextSlot.values()) for (Component l : sg.getText(sl).getMessages(false)) if (!l.getString().isBlank()) lines.add(text(l));
                            if (lines.isEmpty()) continue;
                            j.addProperty("text", cut(String.join(" | ", lines)));
                        } else if (b instanceof Nameable nb && nb.getCustomName() != null) j.addProperty("name", text(nb.getCustomName()));
                        else if (tid == null || tid.getNamespace().equals("minecraft")) continue;
                        else {
                            try { CompoundTag t = b.saveWithoutMetadata(lv.registryAccess()); if (!t.isEmpty()) j.addProperty("data", cut(t.toString())); } catch (Exception ignored) { }
                        }
                        j.addProperty("type", tid == null ? "?" : tid.toString());
                        bes.add(be.getKey().getX() + "," + be.getKey().getY() + "," + be.getKey().getZ(), j);
                    }
                }
            TreeMap<String, Integer> bc = new TreeMap<>();
            for (BlockPos q : BlockPos.betweenClosed(c.offset(-8, -8, -8), c.offset(8, 8, 8))) {
                Identifier bid = BuiltInRegistries.BLOCK.getKey(lv.getBlockState(q).getBlock());
                if (!bid.getNamespace().equals("minecraft")) bc.merge(bid.toString(), 1, Integer::sum);
            }
            bc.forEach(blocks::addProperty);
        }
        mod.add("player_tags", tags);
        mod.add("advancements", adv);
        mod.add("entities", ents);
        mod.add("block_entities", bes);
        mod.add("blocks_near", blocks);
        return mod;
    }

    static <T> List<T> first(Collection<T> c, int n) { List<T> l = new ArrayList<>(c); return l.subList(0, Math.min(n, l.size())); }

    static <T> void rule(JsonObject out, GameRules rules, GameRule<T> r) {
        T v = rules.get(r);
        if (v != null && !v.equals(r.defaultValue())) {
            JsonObject j = new JsonObject(); j.addProperty("value", r.serialize(v)); j.addProperty("default", r.serialize(r.defaultValue()));
            out.add(r.id(), j);
        }
    }

    static <T> void regs(JsonObject out, String kind, Registry<T> reg) {
        TreeMap<String, List<String>> by = new TreeMap<>();
        for (Identifier k : reg.keySet()) if (!vanillaNs(k.getNamespace())) by.computeIfAbsent(k.getNamespace(), x -> new ArrayList<>()).add(k.getPath());
        for (var e : by.entrySet()) {
            JsonObject ns = out.has(e.getKey()) ? out.getAsJsonObject(e.getKey()) : new JsonObject();
            List<String> ids = e.getValue(); ids.sort(String::compareTo);
            JsonArray a = new JsonArray(); ids.stream().limit(MAX_LIST).forEach(a::add);
            JsonObject j = new JsonObject(); j.addProperty("count", ids.size()); j.add("ids", a);
            ns.add(kind, j); out.add(e.getKey(), ns);
        }
    }
}
