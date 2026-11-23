# GameAnything-Bench on your own Docker (host `docker`)

Each trial runs in its own container from the game's image; nothing of a trial survives `docker rm`. The nine open-source games
are baked into the images (licences: [LICENSES.md](LICENSES.md)); the images share one base layer.

| Image `ghcr.io/aetherlabsai/gameanything-bench-<game>` | Game | Install path in the image | GPU |
|---|---|---|---|
| `voxelibre` | VoxeLibre 0.92.4 on Luanti 5.17.0 | /home/player/oss-voxelibre | preferred |
| `xonotic` | Xonotic 0.8.6 | /home/player/oss-xonotic/Xonotic | preferred |
| `godot-tps` | Godot TPS demo (a82f154) on Godot 4.7.2 | /home/player/oss-godot-tps | preferred |
| `flare` | Flare 1.15 | /home/player/oss-flare/squashfs-root | no |
| `tuxemon` | Tuxemon 0.4.35 (9e6258f) | /home/player/oss-tuxemon | no |
| `tof2` | Tanks of Freedom II 1.0.9 (dbe629e) on Godot 4.4.1 | /home/player/oss-tof2 | no |
| `colobot` | Colobot: Gold Edition 0.2.1 | /home/player/oss-colobot | no |
| `celeste64` | Celeste 64 v1.1.1 + .NET 8 SDK (**private only**, see LICENSES.md) | /home/player/oss-celeste64 | no |
| `supertuxkart` | SuperTuxKart 1.5 | /home/player/oss-supertuxkart | preferred |
| `stardew_valley` | **no game**: your own Stardew Valley 1.6.15 + SMAPI 4.5.2, mounted read-only (below) | /lab/game (mount) | no |
| `minecraft` | runtime only (Temurin JDK 25 + Fabric API) for Minecraft Java 26.3 + Fabric 0.19.5; **game files mounted, not in the image** (below) | /mc/main (read-only mount) | preferred |

Pinned digests: [images.yaml](images.yaml). If the registry packages are not reachable for you, build the images yourself
(below).

## Use

Shortest path: `gab get-game open` (or a game list / `commercial` / `all`): image, game files, config and a start check per
game in one command. The steps it runs:

```bash
pip install -e .                                   # from this folder (the backend reads docker/images.yaml)
gab setup                                          # checks docker + which images are present
gab setup --pull voxelibre,flare                   # or --pull all
gab selfcheck flare                                # start with no dialog, A/A of the probes, negative mods
gab run --name stub --agent stub --task <id> --minutes 15
```

Config keys (`gab setup --set KEY VALUE`, or env `GAB_<KEY>`):

| Key | Default | Meaning |
|---|---|---|
| `docker` | `docker` | the docker CLI (may contain arguments) |
| `docker_host` | "" | `DOCKER_HOST` for it |
| `docker_images` | `docker/images.yaml` | the image manifest |
| `docker_image` | "" | one image for every game (debugging) |
| `docker_tag` | "" | run `<repo>:<tag>` instead of the pinned digest (e.g. `local` after `docker/build.sh`) |
| `docker_pull` | `missing` | `missing` / `always` / `never` |
| `docker_gpus` | `auto` | `auto` (= `--gpus all` when the game prefers a GPU and Docker has the nvidia runtime), `all`, `none`, or a `--gpus` value |
| `docker_network` | `none` | the game has no network |
| `docker_shm` | `1g` | `--shm-size` |
| `docker_run_args` | "" | extra `docker run` arguments, e.g. `--memory 8g --cpus 4` |
| `docker_agent_uid` | `2000` | uid of an agent harness inside the container (user `agent` of the image) |

What a trial container is: `docker run -d --init --network none --shm-size 1g --pids-limit 8192 --security-opt
no-new-privileges [--gpus all] <image>`; its main process `gab-seat` keeps the seat screen (Xvfb :101, 1024x768x24) alive;
everything else is `docker exec` as the image's user `player` (uid 1000). Without a GPU the image's `vglrun` drops VirtualGL's
options and the game renders with Mesa llvmpipe (fine for the 2D games; the 3D ones are slow but work).

## Stardew Valley (commercial: bring your own copy)

The image `gameanything-bench-stardew_valley` holds **no game files**: only the .NET 8 SDK, the offline NuGet feed `/lab/nuget`
(ModBuildConfig 4.3.0, the net6.0 reference packs) and the Content Patcher 2.9.0 source. Your own Linux copy of Stardew Valley
1.6.15 with SMAPI 4.5.2 is bind-mounted **read-only** at `/lab/game` in every trial container (the adapter's `docker_mounts()`);
the adapter copies it into the container and builds the harness mod and Content Patcher there (once per container, a few minutes).

```bash
docker/stardew_valley/get-game.sh        # YOUR terminal: SteamCMD (in a small container) asks for your Steam password + Steam Guard
                                         # code itself, downloads app 413150 for Linux (1.6.15 checked by sha256), installs SMAPI 4.5.2
docker/build.sh stardew_valley           # the runtime image (tag local; images.yaml uses :local for this game)
                                         # (slow codeload? put the CP 2.9.0 tarball in docker/stardew_valley/cache/ first)
export GAB_STARDEW_GAME_DIR=/path/to/stardew_valley/game        # or: gab setup --set stardew_game_dir PATH
docker/stardew_valley/decompile.sh       # optional: decompiled sources for the agents -> <game>/../sd-sources, mounted at /lab/sd-sources
gab selfcheck stardew_valley
```

- The Steam client is not needed at run time (SMAPI writes `steam_appid.txt`; the game logs "Steam isn't loaded" and runs).
- Already have the game (GOG / Steam elsewhere)? Copy the Linux 1.6.15 folder anywhere and run
  `docker/stardew_valley/install-smapi.sh <folder>` (SMAPI 4.5.2 from its GitHub release, sha256 pinned).
- Folder modes: keep the parents private (700); the files inside the game folder must be readable by uid 1000 (the
  container's game user), e.g. `chmod -R go+rX <game folder>`.
- The game, the decompiled sources and screenshots never go into an image or the repository.
## Minecraft (commercial: bring your own copy)

The `minecraft` image has no game files. If you own Minecraft: Java Edition, lay out the files from the public endpoints (Mojang
piston-meta + Fabric meta / maven; no login, every file hash-checked, the client jar against the adapter's pinned sha256) and
point gab at them; the backend mounts the folder read only at `/mc/main` in every minecraft container:

```bash
python3 -I docker/minecraft/fetch_game.py ~/gab-data/minecraft      # ~630 MB: versions/26.3, versions/fabric-26.3-0.19.5, libraries/, assets/
gab setup --set minecraft_dir ~/gab-data/minecraft        # (or env GAB_MINECRAFT_DIR)
docker/build.sh minecraft                                            # local image (tag `local`, the manifest entry has no digest yet)
gab selfcheck minecraft
```

The folder must be readable by the container's `player` (uid 1000): keep the files world-readable and protect a parent folder
(e.g. mode 700) instead. The game runs offline (local player name, dead proxy for Mojang services, `--network none`); no
Microsoft account is used anywhere.

## Agent inside the container (`--agent-in-container`)

```bash
export OPENROUTER_API_KEY=...        # in YOUR shell on the host; never written anywhere, never passed into a container
gab run --name codex-qwen --agent codex --agent-in-container \
    --base-url https://openrouter.ai/api/v1 --api-key-env OPENROUTER_API_KEY --model qwen/qwen3.8-27b --task <id>
# OpenAI directly:  --base-url https://api.openai.com/v1 --api-key-env OPENAI_API_KEY --model gpt-...
```

- The harness runs in the trial container as user `agent` (uid 2000), with the trial's work dir and a fresh home bind-mounted
  at the same paths as on the host. `game` and `transcribe` inside are shims that run the host's tools over a unix socket.
- The model key stays on the host: gab starts a small proxy (`gab/host/keyproxy.py`) on a second unix socket; it adds the key
  and forwards only the model API paths (`/responses`, `/chat/completions`, `/models`, ...). Inside the container it appears as
  `http://127.0.0.1:18080/<base path>`; the harness's key variable holds the placeholder `gab-proxy-no-key`. The container keeps
  `--network none`.
- Codex: the official static Linux build (rust-v0.160.0, sha256-checked) is downloaded once into `~/.gab/agents/` and copied
  into the container (`--set codex_container_binary PATH` to use your own); it runs with its own sandbox off, because the
  container is the sandbox. Codex needs a Responses-API endpoint: OpenRouter's `/api/v1/responses` and OpenAI's `/v1/responses` work.
- Without `--base-url` the run is refused: a login file (ChatGPT login, `auth.json`) is never copied into a container.
- Linux Docker Engine: works as is. Docker Desktop (macOS / Windows): bind-mounted unix sockets may not be reachable from the VM;
  run gab inside WSL 2 with Docker Engine, or use `--agent-cmd` with your own route.

### Claude Code inside the container (subscription)

`--agent claude --agent-in-container` runs Claude Code inside the trial container with your **own** Claude subscription.

```bash
claude setup-token                   # in YOUR terminal, once: prints a long-lived subscription token (shown only once)
scripts/gab-session                  # type it at the CLAUDE_CODE_OAUTH_TOKEN prompt (hidden input; never in a file / argv)
gab run --name sonnet-c --agent claude --model claude-sonnet-5-5 --agent-in-container --task <id>
#   = --auth subscription (default): token from CLAUDE_CODE_OAUTH_TOKEN (another variable: --api-key-env NAME)
# an Anthropic API key instead (sent as x-api-key), optionally an Anthropic-compatible gateway:
gab run ... --agent claude --agent-in-container --auth api --api-key-env ANTHROPIC_API_KEY [--base-url https://...]
```

```
 host (gab process)                                   trial container (--network none, user agent uid 2000)
 ------------------                                   ---------------------------------------------------
 CLAUDE_CODE_OAUTH_TOKEN (env, memory only)           /opt/gab-agent/bin/claude  (the host's native Claude Code, docker cp)
 keyproxy (api=anthropic, auth=oauth)  <-- llm.sock --  llm_forward.py 127.0.0.1:18080  <--  ANTHROPIC_BASE_URL
   drops the client's Authorization / x-api-key          CLAUDE_CODE_OAUTH_TOKEN=gab-proxy-no-key (placeholder: OAuth mode)
   adds Authorization: Bearer <token>                    HOME = <trial>/agent_home (fresh: no user settings / login)
   keeps anthropic-beta / -version                       telemetry, auto-update, error reports off
   allows only /v1/messages, /v1/messages/count_tokens
   -> https://api.anthropic.com
 ToolBridge (game, transcribe)         <-- tools.sock -- `game` shim
```

- Same command line as the host claude agent (gab/agent/claude_code/adapter.py): tools Bash/Read/Write/Edit/Glob/Grep,
  `--permission-mode dontAsk` with the same allow / deny rules, `--setting-sources ""`, stream-json transcript, usage and
  cost from the `result` event -- so host and container runs are comparable. Sessions live in the trial's agent home, so
  until_done resumes them as on the host.
- Binary: the host's Claude Code (config `claude`, resolved to the native build, e.g. `~/.local/share/claude/versions/2.1.318`;
  a glibc ELF that runs in the Ubuntu 24.04 game images). `--set claude_container_binary PATH` for another build; an npm
  (node) install is refused (the images have no node).
- Host-side Claude Code processes started from that gab-session shell (judges, play verifier, a host `--agent claude`) see
  CLAUDE_CODE_OAUTH_TOKEN too, and Claude Code then uses that subscription instead of the stored `claude login`.
- Proxy log `<trial>/agent/llm_proxy.log`: method, path, status, bytes, seconds -- never headers, query strings or bodies;
  a path Claude Code needs that is not allowed shows up there as `403 denied`.
- Tests: tests/test_claude_container.py (`GAB_DOCKER_SMOKE=1` runs the real binary in a godot-tps container against a fake
  local Messages API).

## Build the images yourself

```bash
docker/build.sh voxelibre flare          # base + these games, tag `local`   (docker/build.sh all)
gab setup --set docker_tag local
```

`PLAYER_UID=$(id -u) docker/build.sh ...` builds the game user with your uid (only needed if you mount your own files).
Every download is pinned (sha256 / sha512 / git commit, see LICENSES.md); apt is pinned to snapshot.ubuntu.com 2026-10-29.

## First-run dialogs (pre-answered in the image)

- Xonotic: XonStat Terms of Service accepted for the benchmark runtime with statistics off
  (`cl_allow_uid2name 0`, `cl_allow_uidtracking 0`) and a player name, in `~/.xonotic/data/config.cfg` and
  `/home/player/oss-xonotic/client/seed-config.cfg` (the adapter seeds every instance userdir the same way).
- SuperTuxKart: `enable_internet = 2` (= "No"), hardware reports off, local player "Player" -- `seed_cfg/` and `~/.config/supertuxkart/config-0.10/`.
- .NET (Celeste 64): `DOTNET_CLI_TELEMETRY_OPTOUT=1`, `DOTNET_NOLOGO=1`.
- The other games have no ToS / EULA / first-run dialog; each instance gets a fresh home from the adapter.

## Steam games: Terraria (tModLoader), Valheim (BepInEx)

Both need a running Steam client logged on with an account that owns them (yours), and Steam
allows one login at a time, so each machine runs **one Steam client, the hub**, and every Terraria / Valheim trial container
uses it (gab/host/steam.py has the details and the measurements).

```
 host: <steam_state>/  (mode 700, owner = you; so are home/ vnc/ relay/)
   home/   Steam's HOME: the client (downloaded on first start), login token (config/), library (steamapps/)
   vnc/    vnc.sock (600) -- only while `gab steam login` runs
   relay/  <port>.sock (600) + ports: the hub's Steam IPC port(s) as unix sockets

 Steam hub `gab-steam-hub`  --network bridge (Steam needs it), --ipc shareable, user = your uid (image built so)
   Xvfb :101, dbus, Steam client, gab-steam-relay hub (127.0.0.1:57343 of the client -> relay/57343.sock)

 trial container (Terraria / Valheim)  --network none, the hub's pid namespace, same image (same uid)
   --ipc container:gab-steam-hub      Steam's IPC shared memory (/dev/shm/u<uid>-ValveIPCSharedObj-Steam, ...)
   --pid container:gab-steam-hub      Steam watches each game by pid (else it drops the game's pipe within seconds)
   relay/ (ro) + gab-steam-relay trial  127.0.0.1:57343 in the trial -> relay/57343.sock -> the client
   steamapps/common (ro), linux64/steamclient.so -> ~/.steam/sdk64 (ro), ~/.steam/steam.pid = 1
```

Never in a trial: Steam's config/ (login token), userdata/ (cloud saves), logs, the rest of the client, the hub's network. Never
in an image: the Steam client, a game file, a login.

### Once per machine (one command)

```bash
gab get-game terraria,valheim                   # asks only for the SSA, sudo (AppArmor
                                                # profile of the hub), the Steam login (QR code or account in the terminal), EULAs
gab steam probe --game valheim --appid 892970 --n 3 --hold 120   # 3 trial containers, one login, at once
```

What it does, step by step (each skipped when done; the single steps are also commands):
`STEAM_ACCEPT_SSA=yes docker/build.sh steam` (the image's game user = YOUR uid: every file Steam writes on the host is yours) ->
the hub's user-namespace permission (`sudo install -m 644 docker/steam/gab-steam-hub.apparmor /etc/apparmor.d/gab-steam-hub &&
sudo apparmor_parser -r -W /etc/apparmor.d/gab-steam-hub`, config `steam_hub_seccomp docker/steam/seccomp-steam.json`,
`steam_hub_apparmor gab-steam-hub`; without sudo: `steam_hub_apparmor unconfined`) -> `gab steam init` + `gab steam up` ->
`gab steam login` (terminal; `--vnc` = Steam's window over `ssh -N -L 5901:<steam_state>/vnc/vnc.sock <you>@<server>`) ->
`gab steam install terraria` (Steam's own install dialogs, Install / EULA Accept clicked on the hub's screen after your yes; the
buttons are found on a screenshot) -> a hub restart without VNC -> `gab game check terraria`.

The login stays valid while Steam's refresh token does (months; renewed while the hub is online). Redo `gab steam login` after a
password change, "deauthorize all devices", a Steam Guard reset, or when `gab steam status` shows `logged_on: false` after
`gab steam up`. The hub restarts with Docker (`--restart unless-stopped`) and logs on again from the token by itself.

Do NOT change Steam Cloud settings in the hub's Steam: the global switch roams to the account. Trials
use their own save folders, never Steam Cloud.

Security trade-offs: a Steam-game trial shares the hub's IPC AND pid namespaces (Steam needs both) and the
game runs as the same uid as the Steam client. So code of a mod under test (it runs inside the game process) can, as that uid:
use the Steam API as the logged-on account (incl. ISteamHTTP, whose requests the online client makes for it), see the hub's and
the other Steam trials' processes, signal them, and read files through /proc/<pid>/root -- including Steam's login token in the
hub. The agent harness itself (uid 2000) cannot (different uid). The trial still has no network of its own. A leak would have to
go through what the agent sends to its model API. The alternative would be one Steam game per hub at a time.

Config: `docker_steam` (hub | off), `steam_state`, `steam_hub` (container name), `steam_hub_network` (bridge), `steam_hub_image`,
`steam_hub_seccomp`, `steam_hub_apparmor` (both "" = Docker defaults = Steam does not start), `steam_require_logon` (true).
