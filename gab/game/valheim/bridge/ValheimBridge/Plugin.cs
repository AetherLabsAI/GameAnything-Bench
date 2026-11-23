// ValheimBridge: lets an external agent / test harness observe and control the local player in Valheim.
// Speaks WCP v0.1 (WorldCritic Control Protocol): newline-delimited JSON-RPC 2.0 over TCP on two ports:
//   agent channel   VHBRIDGE_PORT          (default 6524): hello, health, get_state, list_actions, act
//   harness channel VHBRIDGE_HARNESS_PORT  (default agent+100): + pause, resume, advance, reset, eval_query
// All game access happens on Unity's main thread: requests are queued by socket threads and served in Update().
using System;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using BepInEx;
using HarmonyLib;
using UnityEngine;

namespace ValheimBridge
{
    [BepInPlugin("worldcritic.valheimbridge", "ValheimBridge", Version)]
    public class Plugin : BaseUnityPlugin
    {
        public const string Version = "0.2.0";
        public static Plugin Instance;

        public class Request
        {
            public object Id; public string Method; public Dictionary<string, object> Params; public bool Harness;
            public string Reply; public ManualResetEvent Done = new ManualResetEvent(false);
        }
        readonly Queue<Request> queue = new Queue<Request>();

        void Awake()
        {
            Instance = this;
            DontDestroyOnLoad(gameObject);
            // VHBRIDGE_SAVEDIR: per-instance save root (worlds_local, characters_local, wc_templates), so several game
            // processes under one Steam client never share world / character files (same as the dedicated server's -savedir)
            var saveDir = Environment.GetEnvironmentVariable("VHBRIDGE_SAVEDIR");
            if (!string.IsNullOrEmpty(saveDir)) { Directory.CreateDirectory(saveDir); Utils.SetSaveDataPath(saveDir); Logger.LogInfo("save dir " + saveDir); }
            int agentPort = EnvInt("VHBRIDGE_PORT", 6524);
            int harnessPort = EnvInt("VHBRIDGE_HARNESS_PORT", agentPort + 100);
            Extra.FpsCap = EnvInt("VHBRIDGE_FPS", -1);      // frame cap for lab / eval instances (CPU); -1 = the game's own setting
            new Harmony("worldcritic.valheimbridge").PatchAll(typeof(Plugin).Assembly);
            Listen(agentPort, false); Listen(harnessPort, true);
            Logger.LogInfo("ValheimBridge " + Version + " (wcp/0.1) agent 127.0.0.1:" + agentPort + ", harness 127.0.0.1:" + harnessPort);
        }

        static int EnvInt(string k, int def) { var v = Environment.GetEnvironmentVariable(k); int r; return int.TryParse(v, out r) ? r : def; }
        public void Log(string msg) { Logger.LogInfo(msg); }
        public void Warn(string msg) { Logger.LogWarning(msg); }

        void Listen(int port, bool harness)
        {
            var t = new Thread(() =>
            {
                var listener = new TcpListener(IPAddress.Loopback, port); listener.Start();
                while (true)
                {
                    TcpClient client;
                    try { client = listener.AcceptTcpClient(); } catch (Exception e) { Logger.LogWarning("accept: " + e.Message); continue; }
                    var ct = new Thread(() => Serve(client, harness)) { IsBackground = true }; ct.Start();
                }
            }) { IsBackground = true, Name = "ValheimBridge-" + (harness ? "harness" : "agent") };
            t.Start();
        }

        void Serve(TcpClient client, bool harness)
        {
            try
            {
                var stream = client.GetStream();
                var reader = new StreamReader(stream, Encoding.UTF8);
                var writer = new StreamWriter(stream, new UTF8Encoding(false)) { AutoFlush = true };
                string line;
                while ((line = reader.ReadLine()) != null)
                {
                    if (line.Trim().Length == 0) continue;
                    var req = new Request { Harness = harness };
                    try
                    {
                        var msg = MiniJson.Parse(line) as Dictionary<string, object>;
                        if (msg == null || !msg.ContainsKey("method")) throw new FormatException("need a JSON object with 'method'");
                        req.Id = msg.ContainsKey("id") ? msg["id"] : null;
                        req.Method = msg["method"] as string;
                        req.Params = msg.ContainsKey("params") ? msg["params"] as Dictionary<string, object> ?? new Dictionary<string, object>() : new Dictionary<string, object>();
                    }
                    catch (Exception e) { writer.WriteLine(Wcp.Error(null, Wcp.PARSE, e.Message)); continue; }
                    lock (queue) queue.Enqueue(req);
                    if (!req.Done.WaitOne(300000)) req.Reply = Wcp.Error(req.Id, Wcp.INTERNAL, "timeout waiting for the game main thread");
                    writer.WriteLine(req.Reply);
                }
            }
            catch (Exception e) { Logger.LogWarning("client error: " + e.Message); }
            finally { client.Close(); }
        }

        void Update()
        {
            while (true)
            {
                Request r = null;
                lock (queue) if (queue.Count > 0) r = queue.Dequeue();
                if (r == null) break;
                var req = r;
                Action<string> ok = res => { req.Reply = Wcp.Result(req.Id, res); req.Done.Set(); };
                Action<int, string> fail = (code, msg) => { req.Reply = Wcp.Error(req.Id, code, msg); req.Done.Set(); };
                try { Commands.Dispatch(req, ok, fail); }
                catch (WcpException e) { fail(e.Code, e.Message); }
                catch (KeyNotFoundException e) { fail(Wcp.INVALID_PARAMS, e.Message); }
                catch (FormatException e) { fail(Wcp.INVALID_PARAMS, e.Message); }
                catch (InvalidCastException e) { fail(Wcp.INVALID_PARAMS, e.Message); }
                catch (Exception e) { Logger.LogWarning(e.ToString()); fail(Wcp.INTERNAL, e.GetType().Name + ": " + e.Message); }
            }
            try { Session.Tick(); } catch (Exception e) { Logger.LogWarning("session tick: " + e); }
            Control.Tick();
            Extra.TickFps();
        }
    }

    public class WcpException : Exception { public int Code; public WcpException(int code, string msg) : base(msg) { Code = code; } }

    public static class Wcp
    {
        public const string Protocol = "wcp/0.1";
        public const int PARSE = -32700, INVALID_REQUEST = -32600, METHOD_NOT_FOUND = -32601, INVALID_PARAMS = -32602, INTERNAL = -32603;
        public const int NOT_READY = 1001, FORBIDDEN = 1002, UNAVAILABLE = 1003, ACTION_FAILED = 1004, TIMEOUT = 1005, VERSION_MISMATCH = 1006, GAME_CRASHED = 1007;
        public static string Result(object id, string resultJson) { return "{\"jsonrpc\":\"2.0\",\"id\":" + Json.Val(id) + ",\"result\":" + resultJson + "}"; }
        public static string Error(object id, int code, string msg) { return "{\"jsonrpc\":\"2.0\",\"id\":" + Json.Val(id) + ",\"error\":" + Json.Obj("code", code, "message", msg) + "}"; }
    }
}
