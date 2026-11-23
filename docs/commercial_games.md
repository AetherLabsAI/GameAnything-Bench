# Commercial games

Five of the 14 games are commercial: Minecraft (Java 26.3 + Fabric), Stardew Valley (1.6.15 + SMAPI 4.5.2), Terraria
(tModLoader), Valheim (BepInEx) and Cities: Skylines. You need your own copies. Their docker images hold only the runtime
(SDKs, mod loaders); the game files come from your account, live on your machine (`commercial_dir`, mode 700) and are
mounted read-only into each trial container. No game file and no login ever goes into an image, a log or the results.

```bash
gab get-game commercial          # or a list: gab get-game minecraft,stardew_valley
gab get-game all --no-check      # every game, without the start checks
```

Per game, `gab get-game` builds the runtime image, fetches the game files and sets the config key that points gab at them,
then checks that the game starts (each step is skipped when already done):

| Game | Game files | Login |
|---|---|---|
| Minecraft | official Mojang / Fabric endpoints, every file hash-checked (`docker/minecraft/fetch_game.py`) | none; the game runs offline |
| Stardew Valley | SteamCMD in a small container (`docker/stardew_valley/get-game.sh`); or your own Linux 1.6.15 folder + `install-smapi.sh` | your Steam account, typed into SteamCMD's own prompt |
| Terraria, Valheim, Cities: Skylines | the machine's Steam hub container (`gab steam ...`) | your Steam account, once per machine (`gab steam login`: QR code or account in the terminal) |

## The Steam hub

Terraria, Valheim and Cities: Skylines need a running Steam client logged on with an account that owns them, and Steam allows
one login at a time. So each machine runs one Steam client container, the hub, and every trial container of these games uses
it: `gab steam init | up | login | status | install | probe` (`gab steam --help`). The login token stays in `steam_state`
on your machine and renews itself while the hub runs; redo `gab steam login` after a password change or a Steam Guard reset.
Building the hub image asks you to accept the Steam Subscriber Agreement, and the hub needs a user-namespace permission
(an AppArmor profile installed with sudo, or `gab setup --set steam_hub_apparmor unconfined`). Details, the container layout
and the security trade-offs: [docker/README.md](../docker/README.md#steam-games-terraria-tmodloader-valheim-bepinex).

Do not change Steam Cloud settings in the hub; trials use their own save folders.

## Without commercial games

`--track open` (on `gab bench`, `run`, `edit`, `eval`, `media fetch`, ...) uses only the 9 open-source games: 77 of the 125
Construction tasks and 64 of the 80 Editing tasks.
