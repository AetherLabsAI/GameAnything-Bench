**Question.** Is the adapted experience BUILT ON and PLAYED THROUGH the host game — rendered in the host's world/engine, played
with the host's avatar, controls and camera, assembled from the host's own systems reused as building blocks (blocks, entities /
mobs, maps, NPC dialog, items, HUD / UI), and keeping the host's look — or is it a mod-drawn replacement that bypasses the host
and merely boots inside it?

**Not required: the host's genre activities.** Our tasks deliberately ask for a different experience built inside the host. The
host's typical genre activities (mining and crafting, fragging, turn-based tactics, monster battles, farming...) are NOT required.
A chase through a Minecraft world with no mining, a sightseeing walk in Xonotic with no frags, or a story told on Flare maps with no
combat can all score 1, as long as they are played through the host's world, avatar, controls and systems. New content built from
host building blocks — custom entities, levels, maps, NPCs, items, dialog inside the host engine — counts as host use. Judge only
host use, not whether the task or the reference is realized well.

**The task.** You get the developer instruction of the task and its reference modality (not the reference itself). Use them only
to know what experience was asked for, and therefore which host features are applicable.

**How to mark each feature** (the per-game features ID1–ID5 below), from the saved evidence (replay screenshots, game-state JSON
snapshots — `engine` / `mod` / `screen_text` — and play-session screenshots / actions):
- **yes**: the adapted experience uses this host layer.
- **partial**: used for only part of the session, or partly replaced.
- **no**: ONLY when the experience actively replaces or bypasses this host layer — e.g. a custom camera and control scheme instead
  of the host's, a full-screen mod UI / canvas instead of the host world, mod-drawn art instead of the host's rendering.
- **not_applicable**: the task does not call for this host feature (e.g. combat or crafting systems in a story or sightseeing
  task). Never mark such a feature "no".
- **unknown**: the evidence never reaches it or cannot show it (no in-game screenshot, the state call failed, the session never
  got there). Never mark such a feature "no".
Do not credit a feature just because the stock game is still installed or reachable from a menu (the regression gate already
checks that); credit it only when it is part of the adapted experience.

You will also receive one vanilla (stock, unmodded) screenshot of the host game as a style/perspective reference. It shows what
the host looks like, to help you judge ID2 and ID5; it is not a target the mod must match.

**State and screenshots must agree.** For games without a directly controlled avatar (cities_skylines, widelands) or with limited camera state (colobot), host-system use is read mainly from state deltas (date/game time advancing, counts of buildings, roads, wares, workers, population, objects). A state delta alone is NOT enough: background simulation can keep running while the player spends the session in a mod-drawn screen. Credit a feature only when the state deltas and the screenshots agree (the screenshots show the host's map view, toolbar or HUD while those numbers change).

| Score | Anchor |
|---|---|
| **1** | Built on and played through the host: the session takes place in the host's world / scene (ID1) with the host's avatar, controls and camera (ID2), host systems are reused as building blocks (ID3/ID4), and it is rendered by the host's engine in the host's look or a restyle the host still renders (ID5). New content built from host building blocks (custom entities / levels / NPCs / items inside the host engine) is host use. Mod overlays (captions, extra HUD text, short cutscene cards) are fine when the session is still played in the host world. |
| **0.5** | Mixed: a substantial part of the session bypasses the host — mod-drawn full screens / canvases, or a custom camera and control scheme replacing the host's — while the host world is still partly used (e.g. the player alternates between host play and long stretches in a mod-drawn screen; or the host world is shown but navigated through a custom camera/controls and mod UI that dominate). |
| **0** | Graft: the host boots, but the session is spent in a full-screen mod canvas or a self-contained scene with its own movement, rules and art; the host world, avatar and systems are not used (host `engine` fields stay idle or at defaults while only `mod` fields / mod UI text change). The host is just the launcher. |

**Official score (fixed rule).** You still mark every feature and give your own holistic score; the official
host-fidelity score is then derived from your feature marks by this rule (your holistic score is kept as a cross-check).
"not_applicable" and "unknown" are ignored; J = the features marked yes / partial / no.
- fewer than 2 features in J → "insufficient evidence" (excluded from the average, counted);
- ID1 is "no", or ID2 is "no", or no feature in J is "yes" → **0**;
- any feature in J is "no", or more than half of J is "partial" → **0.5**;
- otherwise → **1**.
So mark each feature carefully: the marks decide the score.

**Evidence gaps.** If fewer than two features can be judged (marked yes / partial / no — "unknown" and "not_applicable" do not
count), output "insufficient evidence" instead of a score. Insufficient-evidence trials are excluded from the host-fidelity
average, and their number is reported separately.

**Examples.**

1. *0 — Terraria, card game in a panel.* The mod opens a full-screen mod UI on world load in which a self-drawn card battle is played
   with the mouse. Across all snapshots `engine.player.pos` never changes and only `mod.ui` / `screen_text "ui: ..."` change; the
   screenshots show the card table covering the world → **0**.
2. *0 — SuperTuxKart, shooter on the start line.* The add-on's track script draws a top-down space shooter as GUI overlay; the kart
   sits on the grid with `engine.karts[].speed` ≈ 0 the whole session; the track is hidden behind the overlay → **0**.
3. *0.5 — Stardew Valley.* The farmer walks the stock town top-down with WASD and talks to villagers, but the reference's "fishing
   tournament" is a custom full-screen `minigame` screen that occupies most of the session.
4. *1 — Minecraft chase.* The player runs through a custom-built block map in first person with vanilla controls and HUD, chased by
   husks; no mining or crafting (the task does not ask for them: not_applicable) → **1**.
