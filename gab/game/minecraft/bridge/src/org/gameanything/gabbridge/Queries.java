package org.gameanything.gabbridge;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import net.minecraft.client.Minecraft;
import net.minecraft.client.server.IntegratedServer;
import net.minecraft.commands.CommandSource;
import net.minecraft.core.BlockPos;
import net.minecraft.network.chat.Component;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.Container;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.block.entity.BlockEntity;
import net.minecraft.world.level.levelgen.Heightmap;
import net.minecraft.world.phys.AABB;

import java.util.TreeMap;
import java.util.concurrent.TimeUnit;

/** Server commands and read-only world queries (regression probes, dev kit). Everything runs on the server thread. */
final class Queries {
    static int i(JsonObject a, String k, int d) { return a.has(k) ? a.get(k).getAsInt() : d; }
    static double d(JsonObject a, String k, double d) { return a.has(k) ? a.get(k).getAsDouble() : d; }

    /** a server command as the server (permission 4); success, result and the command's own feedback (never broadcast) */
    static JsonObject command(Minecraft mc, String cmd, long timeoutMs) throws Exception {
        IntegratedServer s = mc.getSingleplayerServer();
        if (s == null) return GabBridge.err("not in a world");
        return s.submit(() -> {
            JsonArray msgs = new JsonArray();
            boolean[] success = {false}; int[] count = {0}; boolean[] called = {false};
            CommandSource capture = new CommandSource() {
                public void sendSystemMessage(Component m) { msgs.add(m.getString()); }
                public boolean acceptsSuccess() { return true; }
                public boolean acceptsFailure() { return true; }
                public boolean shouldInformAdmins() { return false; }
            };
            JsonObject o = new JsonObject();
            try {
                var src = s.createCommandSourceStack().withSource(capture).withCallback((ok, n) -> { called[0] = true; success[0] |= ok; count[0] += n; });
                s.getCommands().performPrefixedCommand(src, cmd.startsWith("/") ? cmd.substring(1) : cmd);
                o.addProperty("success", called[0] && success[0]); o.addProperty("result", count[0]);
            } catch (Exception e) { o.addProperty("success", false); msgs.add("exception: " + e); }
            o.add("messages", msgs);
            return o;
        }).get(timeoutMs, TimeUnit.MILLISECONDS);
    }

    static <T> String ser(net.minecraft.world.level.gamerules.GameRules rules, net.minecraft.world.level.gamerules.GameRule<T> r) { return r.serialize(rules.get(r)); }

    static ServerPlayer player(IntegratedServer s) {
        var ps = s.getPlayerList().getPlayers();
        return ps.isEmpty() ? null : ps.get(0);
    }

    static JsonObject query(Minecraft mc, String q, JsonObject a) throws Exception {
        IntegratedServer s = mc.getSingleplayerServer();
        if (s == null) return GabBridge.err("not in a world");
        return s.submit(() -> {
            JsonObject out = new JsonObject();
            ServerPlayer p = player(s);
            ServerLevel lv = p != null ? p.level() : s.overworld();
            switch (q) {
                case "block" -> {
                    BlockPos bp = new BlockPos(i(a, "x", 0), i(a, "y", 0), i(a, "z", 0));
                    out.addProperty("block", State.id(lv.getBlockState(bp)));
                }
                case "blocks" -> {          // box -> {"x,y,z": id} for non-air blocks (at most 40k cells)
                    int x1 = i(a, "x1", 0), y1 = i(a, "y1", 0), z1 = i(a, "z1", 0), x2 = i(a, "x2", 0), y2 = i(a, "y2", 0), z2 = i(a, "z2", 0);
                    long n = (long) (Math.abs(x2 - x1) + 1) * (Math.abs(y2 - y1) + 1) * (Math.abs(z2 - z1) + 1);
                    if (n > 40000) return GabBridge.err("box too large (" + n + " cells, max 40000)");
                    JsonObject b = new JsonObject();
                    for (BlockPos bp : BlockPos.betweenClosed(Math.min(x1, x2), Math.min(y1, y2), Math.min(z1, z2), Math.max(x1, x2), Math.max(y1, y2), Math.max(z1, z2))) {
                        var st = lv.getBlockState(bp);
                        if (!st.isAir()) b.addProperty(bp.getX() + "," + bp.getY() + "," + bp.getZ(), State.id(st));
                    }
                    out.add("blocks", b);
                }
                case "heightmap" -> {       // top motion-blocking (no leaves) y per column of a square, rows by z then x
                    int cx = i(a, "cx", 0), cz = i(a, "cz", 0), r = Math.min(i(a, "r", 32), 64);
                    JsonArray rows = new JsonArray();
                    for (int z = cz - r; z <= cz + r; z++) {
                        JsonArray row = new JsonArray();
                        for (int x = cx - r; x <= cx + r; x++) row.add(lv.getHeight(Heightmap.Types.MOTION_BLOCKING_NO_LEAVES, x, z));
                        rows.add(row);
                    }
                    out.addProperty("x0", cx - r); out.addProperty("z0", cz - r); out.add("heights", rows);
                }
                case "entities" -> {        // within r of (x,y,z) (default: the player), optionally one type id
                    double x = d(a, "x", p != null ? p.getX() : 0), y = d(a, "y", p != null ? p.getY() : 64), z = d(a, "z", p != null ? p.getZ() : 0), r = d(a, "r", 32);
                    String type = a.has("type") ? a.get("type").getAsString() : null;
                    JsonArray es = new JsonArray();
                    for (Entity e : lv.getEntities((Entity) null, new AABB(x - r, y - r, z - r, x + r, y + r, z + r), e -> true)) {
                        String id = State.id(e);
                        if (type != null && !id.equals(type)) continue;
                        JsonObject o = new JsonObject();
                        o.addProperty("id", e.getId()); o.addProperty("type", id); o.add("pos", State.pos(e.getX(), e.getY(), e.getZ()));
                        if (e instanceof LivingEntity le) o.addProperty("health", State.r1(le.getHealth()));
                        if (!e.entityTags().isEmpty()) { JsonArray t = new JsonArray(); e.entityTags().forEach(t::add); o.add("tags", t); }
                        es.add(o);
                    }
                    out.add("entities", es);
                }
                case "container" -> {       // items of the block entity at x,y,z ({} when it is no container)
                    BlockEntity be = lv.getBlockEntity(new BlockPos(i(a, "x", 0), i(a, "y", 0), i(a, "z", 0)));
                    TreeMap<String, Integer> c = new TreeMap<>();
                    if (be instanceof Container ct)
                        for (int k = 0; k < ct.getContainerSize(); k++) { ItemStack st = ct.getItem(k); if (!st.isEmpty()) c.merge(State.id(st), st.getCount(), Integer::sum); }
                    JsonObject items = new JsonObject(); c.forEach(items::addProperty);
                    out.add("items", items); out.addProperty("container", be instanceof Container);
                }
                case "spawn" -> {
                    BlockPos sp = s.overworld().getRespawnData().globalPos().pos();
                    out.add("pos", State.pos(sp));
                }
                case "perf" -> {
                    out.addProperty("mspt", Math.round(s.getAverageTickTimeNanos() / 1e4) / 100.0);
                    out.addProperty("fps", mc.getFps());
                }
                case "gamerules" -> {        // every game rule -> its value as the command prints it
                    JsonObject g = new JsonObject();
                    var rules = s.getGameRules();
                    rules.availableRules().forEach(r -> g.addProperty(r.id(), ser(rules, r)));
                    out.add("rules", g);
                }
                case "time" -> {
                    out.addProperty("day_time", s.overworld().getOverworldClockTime());
                    out.addProperty("game_time", s.overworld().getGameTime());
                    out.addProperty("raining", s.overworld().isRaining());
                    out.addProperty("thundering", s.overworld().isThundering());
                }
                default -> { return GabBridge.err("unknown query " + q + " (block, blocks, heightmap, entities, container, spawn, perf, gamerules, time)"); }
            }
            return out;
        }).get(10, TimeUnit.SECONDS);
    }
}
