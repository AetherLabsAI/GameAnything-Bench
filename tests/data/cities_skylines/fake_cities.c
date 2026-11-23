/* A FAKE "Cities.x64" for tests/test_game_cities_skylines.py: not the game. It lets the adapter's shell plumbing (gcs.sh start / state /
   alive / stop, csp.sh prep / run, cgs.py, the instance layout) run end to end on a machine without the game. The runners find the game
   process by its executable name (Cities.x64) and its CS_INSTANCE variable, so this has to be a real binary, not a script.
   - CSProbe role: Addons/Mods/CSProbe/probe.cfg exists -> writes one CSPROBE line per requested group (+ _boot, _entered, _done; run2:
     saveload) to the cfg's output path, then exits (the real CSProbe quits the game after _done). Group "fixture" also writes
     Saves/gafixture.crp and fixture.cfg (next to the output), like CSProbe's fixture group; a probe.cfg line fixture=<path>
     (a run on the loaded fixture) adds a _fixture line when that file exists.
   - GABState role: $XDG_DATA_HOME/gabstate exists -> answers each request file `req` with `ans` (status in_game after a start.cfg
     "new:<map>|<city>" or "load:<save>", menu without one; mods = the folders in Addons/Mods except GABState). A request id ending in
     ":dump" also writes ../stock_prefabs.txt. Map "Blocked Map" plays the legal dialog: writes `blocked`, stays in the menu. */
#include <dirent.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/stat.h>

static void chomp(char *s) { size_t n = strlen(s); while (n && (s[n - 1] == '\n' || s[n - 1] == '\r')) s[--n] = 0; }

static int slurp(const char *p, char *buf, size_t sz) {
    FILE *f = fopen(p, "r"); if (!f) return 0;
    size_t n = fread(buf, 1, sz - 1, f); buf[n] = 0; fclose(f); chomp(buf); return 1;
}

static int probe(const char *cfg, const char *x) {
    char mode[64] = "", groups[1024] = "", out[4096] = "";
    FILE *c = fopen(cfg, "r"); if (!c) return 0;
    if (!fgets(mode, sizeof mode, c) || !fgets(groups, sizeof groups, c) || !fgets(out, sizeof out, c)) { fclose(c); return 0; }
    char extra[4096] = "", fixture[4096] = "";
    while (fgets(extra, sizeof extra, c)) { chomp(extra); if (strncmp(extra, "fixture=", 8) == 0) snprintf(fixture, sizeof fixture, "%s", extra + 8); }
    fclose(c); chomp(mode); chomp(groups); chomp(out);
    FILE *o = fopen(out, "a"); if (!o) return 0;
    fprintf(o, "CSPROBE {\"probe\":\"_boot\",\"mode\":\"%s\"}\n", mode);
    fprintf(o, "CSPROBE {\"probe\":\"_entered\",\"city\":\"GAProbe\",\"map\":\"Green Plains\"}\n");
    if (fixture[0] && access(fixture, R_OK) == 0) fprintf(o, "CSPROBE {\"probe\":\"_fixture\",\"population\":500}\n");
    if (strcmp(mode, "run2") == 0) fprintf(o, "CSPROBE {\"probe\":\"saveload\",\"segments_same\":true,\"city\":\"GAProbe\"}\n");
    else for (char *g = strtok(groups, ","); g; g = strtok(NULL, ",")) {
        if (strcmp(g, "fixture") == 0) {
            char p[8192]; FILE *f;
            snprintf(p, sizeof p, "%s/Colossal Order/Cities_Skylines/Saves", x); mkdir(p, 0755);
            snprintf(p, sizeof p, "%s/Colossal Order/Cities_Skylines/Saves/gafixture.crp", x);
            if ((f = fopen(p, "w"))) { fputs("fake grown city", f); fclose(f); }
            snprintf(p, sizeof p, "%s", out); char *sl = strrchr(p, '/'); if (sl) strcpy(sl, "/fixture.cfg");
            if ((f = fopen(p, "w"))) { fputs("12.5 60 -840 0 1", f); fclose(f); }
            fprintf(o, "CSPROBE {\"probe\":\"fixture\",\"saved\":true,\"population\":500,\"segments\":61}\n");
        } else fprintf(o, "CSPROBE {\"probe\":\"%s\",\"fake\":1}\n", g);
    }
    fprintf(o, "CSPROBE {\"probe\":\"_done\"}\n");
    fclose(o); sleep(2); return 1;
}

int main(void) {
    const char *x = getenv("XDG_DATA_HOME");
    if (!x) { for (;;) pause(); }
    char cfg[4096], dir[4096], mods[4096], p[4096], buf[4096];
    snprintf(mods, sizeof mods, "%s/Colossal Order/Cities_Skylines/Addons/Mods", x);
    snprintf(cfg, sizeof cfg, "%s/CSProbe/probe.cfg", mods);
    if (access(cfg, R_OK) == 0) return probe(cfg, x) ? 0 : 1;
    snprintf(dir, sizeof dir, "%s/gabstate", x);
    for (;;) {
        usleep(40000);
        snprintf(p, sizeof p, "%s/req", dir);
        char rid[256];
        if (!slurp(p, rid, sizeof rid)) continue;
        unlink(p);
        char *colon = strstr(rid, ":dump");
        if (colon) {
            *colon = 0; snprintf(p, sizeof p, "%s/../stock_prefabs.txt", dir);
            FILE *s = fopen(p, "w"); if (s) { fputs("b:Wind Turbine\nn:Basic Road\n", s); fclose(s); }
        }
        const char *status = "menu"; char city[256] = "", map[256] = "";
        snprintf(p, sizeof p, "%s/start.cfg", dir);
        if (slurp(p, buf, sizeof buf)) {
            if (strncmp(buf, "new:", 4) == 0) {
                char *bar = strchr(buf + 4, '|'); if (bar) { *bar = 0; snprintf(city, sizeof city, "%s", bar + 1); }
                snprintf(map, sizeof map, "%s", buf + 4);
                if (strcmp(map, "Blocked Map") == 0) {
                    snprintf(p, sizeof p, "%s/blocked", dir);
                    FILE *b = fopen(p, "w"); if (b) { fputs("DIALOG LegalDisclaimerPanel is showing: not clicked, not started", b); fclose(b); }
                } else status = "in_game";
            } else if (strncmp(buf, "load:", 5) == 0) { status = "in_game"; snprintf(city, sizeof city, "%s", buf + 5); }
        }
        char list[2048] = ""; DIR *d = opendir(mods); struct dirent *e;
        while (d && (e = readdir(d))) {
            if (e->d_name[0] == '.' || strcmp(e->d_name, "GABState") == 0) continue;
            size_t n = strlen(list); snprintf(list + n, sizeof list - n, "%s\"%s\"", n ? "," : "", e->d_name);
        }
        if (d) closedir(d);
        snprintf(p, sizeof p, "%s/ans.tmp", dir);
        FILE *a = fopen(p, "w"); if (!a) continue;
        fprintf(a, "{\"id\":\"%s\",\"running\":true,\"engine\":{\"status\":\"%s\",\"city\":\"%s\",\"map\":\"%s\",\"money\":70000.0,\"mods\":[%s]},"
                   "\"mod\":{},\"screen_text\":[]}", rid, status, city, map, list);
        fclose(a);
        char ans[4096]; snprintf(ans, sizeof ans, "%s/ans", dir); rename(p, ans);
    }
}
