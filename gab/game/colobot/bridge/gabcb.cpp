// gabcb.so -- the gab state reader for Colobot: Gold Edition 0.2.1-alpha (LD_PRELOAD into the player session only, never into
// the regression probes). Read-only: it interposes libCBot / SDL entry points the game already calls, keeps pointers, and every
// 0.25 s (on the game's main thread, inside SDL_GL_SwapWindow) writes one JSON snapshot to $GAB_CB_STATE:
//   objects   every live game object, through the game's own CBOT view of it (the `object` class: the game registers uObject as
//             its update function; we call CBotVar::Update like a CBOT program reading `radar(...).position` does)
//   programs  CBOT programs that ran in the last 2 s: owner object, current function, the variables of its stack (like the
//             game's program editor debugger: CBotProgram::GetStackVars)
//   messages  the last texts shown with CBOT message() (wrapped at CBotProgram::AddFunction("message", ...))
// No gameplay effect: nothing is written into the game, no input, no pause. Unset $GAB_CB_STATE = inert.
// Build: bridge/build.sh (needs the CBot headers of the colobot-gold-0.2.1-alpha tag; the prebuilt gabcb.so is next to it).
#include <dlfcn.h>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <map>
#include <mutex>
#include <set>
#include <string>
#include <vector>
#include "CBot/CBotVar/CBotVar.h"
#include "CBot/CBotVar/CBotVarClass.h"
#include "CBot/CBotClass.h"
#include "CBot/CBotProgram.h"

using namespace CBot;
typedef bool (*RunFn)(CBotVar*, CBotVar*, int&, void*);
typedef CBotTypResult (*CompFn)(CBotVar*&, void*);

static std::recursive_mutex mu;
static std::set<CBotVar*> objs;                    // object vars (class "object") with a live user pointer
static std::map<void*, CBotVar*> byUser;           // CObject* -> its var
static std::map<CBotProgram*, std::pair<double, void*>> progs;   // program -> (last Run time, CScript*)
struct Msg { double t; std::string text; int type; void* obj; };
static std::vector<Msg> msgs;
static RunFn realMessage = nullptr;
static double lastWrite = 0, t0 = 0;
static long frames = 0;

static double now() { timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts); return ts.tv_sec + ts.tv_nsec * 1e-9; }
template <class F> static F real(const char* sym) { return reinterpret_cast<F>(dlsym(RTLD_NEXT, sym)); }

static bool isObjectClass(CBotVar* v) {
    CBotClass* c = v ? v->GetClass() : nullptr;
    return c && c->GetName() == "object";
}

// ---- interposed libCBot entry points (called by the game through the PLT; plain functions under the mangled names, so no
// compiler-generated member code runs, e.g. no member destructors after our ~CBotProgram)
extern "C" void hSetUserPtr(CBotVar* self, void* p) __asm__("_ZN4CBot7CBotVar10SetUserPtrEPv");
extern "C" void hSetUserPtr(CBotVar* self, void* p) {
    static auto f = real<void (*)(CBotVar*, void*)>("_ZN4CBot7CBotVar10SetUserPtrEPv");
    void* old = self->GetUserPtr();
    f(self, p);
    std::lock_guard<std::recursive_mutex> g(mu);
    if (!isObjectClass(self)) return;
    if (p == OBJECTDELETED || p == OBJECTCREATED || p == nullptr) { objs.erase(self); auto it = byUser.find(old); if (it != byUser.end() && it->second == self) byUser.erase(it); }
    else { objs.insert(self); byUser[p] = self; }
}

extern "C" bool hRun(CBotProgram* self, void* pUser, int timer) __asm__("_ZN4CBot11CBotProgram3RunEPvi");
extern "C" bool hRun(CBotProgram* self, void* pUser, int timer) {
    static auto f = real<bool (*)(CBotProgram*, void*, int)>("_ZN4CBot11CBotProgram3RunEPvi");
    { std::lock_guard<std::recursive_mutex> g(mu); progs[self] = {now(), pUser}; }
    return f(self, pUser, timer);
}

extern "C" void hDtor(CBotProgram* self) __asm__("_ZN4CBot11CBotProgramD1Ev");
extern "C" void hDtor(CBotProgram* self) {
    { std::lock_guard<std::recursive_mutex> g(mu); progs.erase(self); }
    static auto f = real<void (*)(CBotProgram*)>("_ZN4CBot11CBotProgramD1Ev");
    f(self);
}

static bool gabMessage(CBotVar* var, CBotVar* result, int& exception, void* user) {
    try {
        Msg m{now(), var ? var->GetValString() : "", 0, user ? *reinterpret_cast<void**>(user) : nullptr};   // CScript::m_object is its 1st member
        if (var && var->GetNext()) m.type = var->GetNext()->GetValInt();
        if (m.text.size() > 200) m.text.resize(200);
        std::lock_guard<std::recursive_mutex> g(mu);
        msgs.push_back(m); if (msgs.size() > 20) msgs.erase(msgs.begin());
    } catch (...) {}
    return realMessage(var, result, exception, user);
}

#define ADDFN "_ZN4CBot11CBotProgram11AddFunctionERKNSt7__cxx1112basic_stringIcSt11char_traitsIcESaIcEEEPFbPNS_7CBotVarESA_RiPvEPFNS_13CBotTypResultERSA_SC_E"
extern "C" bool hAddFunction(const std::string& name, RunFn rExec, CompFn rCompile) __asm__(ADDFN);
extern "C" bool hAddFunction(const std::string& name, RunFn rExec, CompFn rCompile) {
    static auto f = real<bool (*)(const std::string&, RunFn, CompFn)>(ADDFN);
    if (name == "message" && !realMessage) { realMessage = rExec; return f(name, gabMessage, rCompile); }
    return f(name, rExec, rCompile);
}

// ---- snapshot
static std::string esc(const std::string& s) {
    std::string o; o.reserve(s.size() + 2);
    for (unsigned char c : s) {
        if (c == '"' || c == '\\') { o += '\\'; o += c; }
        else if (c < 0x20) { char b[8]; snprintf(b, sizeof b, "\\u%04x", c); o += b; }
        else o += c;
    }
    return o;
}
static std::string num(float v) { if (!std::isfinite(v)) return "null"; char b[32]; snprintf(b, sizeof b, "%.2f", v); return b; }
static int idOf(void* user) { auto it = byUser.find(user); return it == byUser.end() ? -1 : it->second->GetItem("id") ? it->second->GetItem("id")->GetValInt() : -1; }

static std::string val(CBotVar* v, int depth) {
    if (!v) return "null";
    CBotType t = v->GetType();
    if (t <= CBotTypString) {
        if (t == CBotTypFloat || t == CBotTypDouble) return num(v->GetValFloat());
        if (t == CBotTypBoolean) return v->GetValInt() ? "true" : "false";
        std::string s = v->GetValString(); if (s.size() > 200) s.resize(200);
        return t == CBotTypString || t == CBotTypChar ? "\"" + esc(s) + "\"" : (s.empty() ? "null" : s);
    }
    if (t == CBotTypPointer || t == CBotTypClass) {
        CBotVar* p = t == CBotTypPointer ? v->GetPointer() : v;
        if (!p) return "null";
        CBotClass* c = p->GetClass(); std::string cn = c ? c->GetName() : "?";
        if (cn == "object") { void* u = p->GetUserPtr(); return "\"object#" + std::to_string(idOf(u)) + "\""; }
        if (depth > 1) return "\"" + esc(cn) + "\"";
        std::string o = "{\"_class\": \"" + esc(cn) + "\"";
        int n = 0;
        for (CBotVar* i = p->GetItemList(); i && n < 12; i = i->GetNext(), n++) o += ", \"" + esc(i->GetName()) + "\": " + val(i, depth + 1);
        return o + "}";
    }
    if (t == CBotTypArrayPointer || t == CBotTypArrayBody) {
        CBotVar* p = t == CBotTypArrayPointer ? v->GetPointer() : v;
        if (!p || depth > 1) return "\"array\"";
        std::string o = "["; int n = 0;
        for (CBotVar* i = p->GetItemList(); i && n < 12; i = i->GetNext(), n++) o += (n ? ", " : "") + val(i, depth + 1);
        return o + "]";
    }
    return "null";
}

static std::string objJson(CBotVar* v) {
    v->Update(nullptr);                                         // the game's uObject refreshes the fields (as for a CBOT read)
    auto F = [&](const char* k) { CBotVar* i = v->GetItem(k); return i ? num(i->GetValFloat()) : std::string("null"); };
    auto I = [&](const char* k) { CBotVar* i = v->GetItem(k); return i ? i->GetValInt() : 0; };
    auto P = [&](const char* k) -> std::string {
        CBotVar* i = v->GetItem(k); CBotVar* p = i ? i->GetPointer() : nullptr;
        if (!p || !p->GetUserPtr() || p->GetUserPtr() == OBJECTDELETED) return "null";
        CBotVar* c = p->GetItem("category"); return c ? "\"" + esc(c->GetValString()) + "\"" : "null";
    };
    CBotVar* pos = v->GetItem("position");
    std::string ps = "null";
    if (pos && pos->GetItem("x")) ps = "[" + num(pos->GetItem("x")->GetValFloat()) + ", " + num(pos->GetItem("y")->GetValFloat()) + ", " + num(pos->GetItem("z")->GetValFloat()) + "]";
    CBotVar* cat = v->GetItem("category");
    return "{\"id\": " + std::to_string(I("id")) + ", \"type\": \"" + esc(cat ? cat->GetValString() : "?") + "\", \"cat\": " + std::to_string(cat ? cat->GetValInt() : -1) +
           ", \"pos\": " + ps + ", \"orientation\": " + F("orientation") + ", \"energy\": " + F("energyLevel") + ", \"shield\": " + F("shieldLevel") +
           ", \"temperature\": " + F("temperature") + ", \"altitude\": " + F("altitude") + ", \"lifetime\": " + F("lifeTime") +
           ", \"team\": " + std::to_string(I("team")) + ", \"dead\": " + (I("dead") ? "true" : "false") + ", \"cell\": " + P("energyCell") + ", \"load\": " + P("load") + "}";
}

static void writeSnapshot(const char* path) {
    std::lock_guard<std::recursive_mutex> g(mu);
    double t = now();
    std::string o = "{\"t\": " + num(t - t0) + ", \"frames\": " + std::to_string(frames) + ", \"objects\": [";
    int n = 0;
    for (CBotVar* v : objs) { try { o += (n++ ? ", " : "") + objJson(v); } catch (...) {} }
    o += "], \"programs\": [";
    n = 0;
    for (auto& kv : progs) {
        if (t - kv.second.first > 2.0) continue;
        CBotProgram* pr = kv.first; void* script = kv.second.second;
        void* owner = script ? *reinterpret_cast<void**>(script) : nullptr;                 // CScript::m_object (1st member)
        bool sane = script && *reinterpret_cast<CBotProgram**>(reinterpret_cast<char*>(script) + 32) == pr;   // CScript::m_botProg
        std::string fn; int a = 0, b = 0;
        try { pr->GetRunPos(fn, a, b); } catch (...) {}
        std::string vars; int nv = 0;
        if (!fn.empty()) {
            for (int level = 0; level > -8 && nv < 30; level--) {
                std::string f2; CBotVar* var = nullptr;
                try { var = pr->GetStackVars(f2, level); } catch (...) { break; }
                if (f2 != fn) break;
                for (; var && nv < 30; var = var->GetNext()) {
                    std::string nm = var->GetName();
                    if (nm.empty() || nm == "this") continue;
                    vars += (nv++ ? ", " : "") + ("\"" + esc(nm) + "\": ") + val(var, 0);
                }
            }
        }
        o += (n++ ? ", " : "") + ("{\"owner\": " + std::to_string(sane ? idOf(owner) : -1) + ", \"function\": \"" + esc(fn) + "\", \"vars\": {" + vars + "}}");
    }
    o += "], \"messages\": [";
    n = 0;
    for (auto& m : msgs) o += (n++ ? ", " : "") + ("{\"t\": " + num(m.t - t0) + ", \"text\": \"" + esc(m.text) + "\", \"type\": " + std::to_string(m.type) +
                                               ", \"from\": " + std::to_string(idOf(m.obj)) + "}");
    o += "]}\n";
    std::string tmp = std::string(path) + ".tmp";
    if (FILE* f = fopen(tmp.c_str(), "w")) { fwrite(o.data(), 1, o.size(), f); fclose(f); rename(tmp.c_str(), path); }
}

extern "C" void SDL_GL_SwapWindow(void* window) {
    static auto f = real<void (*)(void*)>("SDL_GL_SwapWindow");
    static const char* path = getenv("GAB_CB_STATE");
    f(window);
    if (!path) return;
    frames++;
    double t = now();
    if (t0 == 0) t0 = t;
    if (t - lastWrite >= 0.25) { lastWrite = t; try { writeSnapshot(path); } catch (...) {} }
}

// child processes of the game (it runs `sh` to look for zenity) must not inherit the preload
__attribute__((constructor)) static void gabInit() { unsetenv("LD_PRELOAD"); }
