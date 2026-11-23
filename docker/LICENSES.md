# Licences of the games baked into the GameAnything-Bench images

Checked against the licence files at the exact tag / commit we install. Every image carries the licence and credit
files of its game under `/opt/gab/licenses/<game>/` plus `SOURCE.txt` (where the matching source is). The images are published
to the registry named in [images.yaml](images.yaml) (`<registry>/gameanything-bench-<game>`) under the conditions below
(column "to publish").

| Game (what is installed) | Code | Data / assets | Restricted parts | Verdict | To publish |
|---|---|---|---|---|---|
| VoxeLibre 0.92.4 (git a5078ba) on Luanti 5.17.0 (PPA .deb) | GPL-3.0-or-later; Luanti LGPL-2.1-or-later | CC BY-SA 3.0 / 4.0, CC BY 4.0, CC0 ("no non-free licenses", LEGAL.md) | none (no Mojang assets) | redistributable | keep LICENSE.txt, LEGAL.md, CREDITS.md; source = git tag + Luanti 5.17.0 |
| Xonotic 0.8.6 (official zip, music packs left out) | GPL-3.0-or-later (DarkPlaces GPL-2.0-or-later) | GPL-3.0-or-later (COPYING covers the whole release) | key_0.d0pk = public player-ID key, part of the release; the XonStat ToS is a service agreement (stats / forums), not a game licence -- accepted for the benchmark runtime with statistics off (Wenyi, 2026-10-30) | redistributable | COPYING (+ GPL texts), point to xonotic-0.8.6-source.zip |
| Godot TPS demo (master a82f154) on Godot 4.7.2 | MIT; Godot MIT (+ COPYRIGHT.txt) | CC-BY 3.0 (art, music) | LICENSE.md: some textures derived from GameTextures.com materials, released by the authors under CC-BY (upstream's claim) | redistributable (small uncertainty) | ship LICENSE.md with the CC-BY attribution, Godot LICENSE / COPYRIGHT |
| Flare 1.15 (official AppImage, extracted) | GPL-3.0-or-later | CC-BY-SA 3.0; fonts OFL 1.1 | none | redistributable | GPL + LICENSE.txt + CREDITS.txt; point to flare-engine / flare-game v1.15 |
| Tuxemon 0.4.35 (development 9e6258f) + PyPI deps | GPL-3.0; deps LGPL / MIT / Apache / BSD / HPND | CC-BY-SA, CC-BY, CC0, PD, OFL (ATTRIBUTIONS.md) | some sprites under the "XYG Open Source License v1.1" (text no longer online); one icon by an unknown artist | private OK; **review before publishing** | LICENSE + ATTRIBUTIONS.md; check the XYG / unknown-artist assets first |
| Tanks of Freedom II (master dbe629e, 1.0.9) on Godot 4.4.1 | MIT (also the graphics) | music / sfx CC BY-SA 4.0, CC BY 4.0; Kenney CC0; fonts OFL / Apache | `docs/` marketing media proprietary -> **left out of the image** (CREDITS.md kept) | redistributable | LICENSE.md + docs/CREDITS.md (in image) |
| Colobot Gold 0.2.1 (Ubuntu noble 0.2.1-1build3, universe) | GPL-3.0-or-later | GPL-3.0-or-later (Debian copyright) | none (from Debian main, DFSG-free) | redistributable | keep /usr/share/doc/*/copyright (in image), source via `apt-get source colobot` |
| Celeste 64 v1.1.1 (source, bfc7a3b) + .NET 8 SDK | Source/ MIT | **Content (art, audio) (c) Maddy Makes Games**: only non-commercial mods / fan games are allowed | **FMOD runtime libraries + banks: proprietary**; Renogare font licence unclear | **private only -- never publish** | not publishable as is; a public release would ship a build recipe that fetches the tag on the user's machine, or needs permission from the rights holders |
| SuperTuxKart 1.5 (official Linux x86_64 build) | GPL-3.0-or-later | GPL / CC-BY / CC-BY-SA / CC0 / PD (data/**/licenses.txt; Debian main) | none found | redistributable | COPYING + every licenses.txt (in image), point to SuperTuxKart-1.5-src.tar.gz |
| Stardew Valley (image: **no game files**; .NET 8 SDK, ModBuildConfig 4.3.0, NETCore/AspNetCore.App.Ref 6.0.36, Content Patcher 2.9.0 source) | SDK / packages / CP: MIT | none in the image | the game is commercial ((c) ConcernedApe): the user's own copy is mounted read-only at run time, never baked | image redistributable (no game content); review | nothing of the game; the decompiled sources (decompile.sh) stay on the user's machine |
| Minecraft runtime image (Temurin JDK 25.0.5+8, Fabric API 0.162.0+26.3) -- **no Minecraft files** | JDK GPL-2.0 + Classpath Exception; Fabric API Apache-2.0 | none in the image | the game (Mojang, Minecraft EULA) is the user's own copy, mounted read only at run time | runtime redistributable; the game never | JDK legal/ (in image); never add game files to the image |

Base layer: Ubuntu 24.04 packages under their own licences (`/usr/share/doc/*/copyright`), VirtualGL 3.1.5 (LGPL-2.1 /
wxWindows Library Licence). The gab scripts in `docker/` are part of this repository.

GPL note: a public image of a GPL game must come with the corresponding source or a written offer; linking to the exact upstream
tag / source archive (as in each `SOURCE.txt`) is the usual practice.

Downloads and pins (all checked at build time; a mismatch stops the build):

| Component | Pin |
|---|---|
| ubuntu:24.04 | sha256:fe81560990fae86dc4743202b2cb4d3df98d0bcf949173cecef15ce1127ef852 (apt: snapshot.ubuntu.com 20261029T000000Z) |
| VirtualGL 3.1.5 .deb | sha256 df3f7788ce41b182a47c0d298e5cd6d2d63579522cb41825970b7726e825485e |
| minetest_5.17.0-ppa0~ubuntu24.04.1_amd64.deb | sha256 6692b0ebf5678d1b612b1ad75c2e2622f6717b9c461a1daebde49c5b713158cc |
| VoxeLibre tag 0.92.4 | commit a5078bab2efbc8a670b8b10c290ade73aa87be6a |
| xonotic-0.8.6.zip | sha512 cb39879e…996c2a6 (= dl.xonotic.org/xonotic-0.8.6.sha512) |
| Godot_v4.7.2-stable_linux.x86_64.zip | sha512 9aa00f7a…ed54c65 (SHA512-SUMS.txt) |
| tps-demo | commit a82f15448e9b015440d3bbdf5e10801b260c4e9f |
| flare-linux64-v1.15.AppImage | sha256 155cd3a58f0e510e5bbb9db1a093469c88e52c05295afef53c24854cc5bbaf82 |
| Tuxemon | commit 9e6258ff726b786040a267e8bdbbf037b560285e; PyPI versions in docker/tuxemon/requirements.lock (versions, not hashes) |
| Godot_v4.4.1-stable_linux.x86_64.zip | sha512 ef4e7688…b71370e (SHA512-SUMS.txt) |
| tanks-of-freedom-ii | commit dbe629ea05691599dde4fd65ab9a13b78a4ccfe1 |
| colobot | Ubuntu noble 0.2.1-1build3 (apt, signed snapshot index) |
| Celeste64 tag v1.1.1 | commit bfc7a3ba6f35d25bd11b4c4bad749398f70034e2; dotnet-sdk-8.0 from the snapshot |
| SuperTuxKart-1.5-linux-x86_64.tar.gz | sha256 57090b6c2163eb691f20104ae9712204acbb4e8341059dee0a5ff5315efc401b |
| Codex CLI (copied into a container only with --agent-in-container) | rust-v0.160.0 codex-x86_64-unknown-linux-musl.tar.gz sha256 306865417d4ee7a927785852910a527f41e1e159add390ac5ae3accb67d44a13 (Apache-2.0) |
| pathoschild.stardew.modbuildconfig 4.3.0 .nupkg | sha256 354b7d1bd4d4eb68caf5a5f3ef3508adaed705a9de742fa8e3f09ce7af02b721 |
| microsoft.netcore.app.ref 6.0.36 .nupkg | sha256 f4b66056820517ca8dc94bbc91d26b6062eeb4c17f70bd8af361cddb2c30971f |
| microsoft.aspnetcore.app.ref 6.0.36 .nupkg | sha256 f630e459b8f0fe777ccaa7735536a00aeaabea8c09fc350c8b80655194f88565 |
| StardewMods tag content-patcher/2.9.0 (codeload tarball) | sha256 02320a7b9f54b4ce60c74a3faf2d158c24e0d0c0191774275172c7005cd0b42f |
| SMAPI-4.5.2-installer.zip (get-game.sh / install-smapi.sh, host side, not in an image) | sha256 dd01ddca7b566bfe0d3b3d2d03833496abc56c53da976241f2ab443f5484acc4 |
