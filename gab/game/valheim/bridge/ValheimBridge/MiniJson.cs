// Minimal JSON parser for .NET 3.5 (objects -> Dictionary<string,object>, arrays -> List<object>,
// numbers -> double, strings, bool, null). Enough for WCP requests.
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace ValheimBridge
{
    public static class MiniJson
    {
        public static object Parse(string s) { int i = 0; var v = Value(s, ref i); Ws(s, ref i); if (i != s.Length) throw new FormatException("trailing data at " + i); return v; }

        static void Ws(string s, ref int i) { while (i < s.Length && char.IsWhiteSpace(s[i])) i++; }

        static object Value(string s, ref int i)
        {
            Ws(s, ref i);
            if (i >= s.Length) throw new FormatException("unexpected end");
            char c = s[i];
            if (c == '{') return Obj(s, ref i);
            if (c == '[') return Arr(s, ref i);
            if (c == '"') return Str(s, ref i);
            if (s.Length - i >= 4 && string.CompareOrdinal(s, i, "true", 0, 4) == 0) { i += 4; return true; }
            if (s.Length - i >= 5 && string.CompareOrdinal(s, i, "false", 0, 5) == 0) { i += 5; return false; }
            if (s.Length - i >= 4 && string.CompareOrdinal(s, i, "null", 0, 4) == 0) { i += 4; return null; }
            return Num(s, ref i);
        }

        static Dictionary<string, object> Obj(string s, ref int i)
        {
            var d = new Dictionary<string, object>(); i++; Ws(s, ref i);
            if (i < s.Length && s[i] == '}') { i++; return d; }
            while (true)
            {
                Ws(s, ref i); var k = Str(s, ref i); Ws(s, ref i);
                if (i >= s.Length || s[i] != ':') throw new FormatException("expected ':' at " + i);
                i++; d[k] = Value(s, ref i); Ws(s, ref i);
                if (i < s.Length && s[i] == ',') { i++; continue; }
                if (i < s.Length && s[i] == '}') { i++; return d; }
                throw new FormatException("expected ',' or '}' at " + i);
            }
        }

        static List<object> Arr(string s, ref int i)
        {
            var l = new List<object>(); i++; Ws(s, ref i);
            if (i < s.Length && s[i] == ']') { i++; return l; }
            while (true)
            {
                l.Add(Value(s, ref i)); Ws(s, ref i);
                if (i < s.Length && s[i] == ',') { i++; continue; }
                if (i < s.Length && s[i] == ']') { i++; return l; }
                throw new FormatException("expected ',' or ']' at " + i);
            }
        }

        static string Str(string s, ref int i)
        {
            if (s[i] != '"') throw new FormatException("expected string at " + i);
            i++; var sb = new StringBuilder();
            while (i < s.Length)
            {
                char c = s[i++];
                if (c == '"') return sb.ToString();
                if (c != '\\') { sb.Append(c); continue; }
                char e = s[i++];
                switch (e)
                {
                    case 'n': sb.Append('\n'); break; case 't': sb.Append('\t'); break; case 'r': sb.Append('\r'); break;
                    case 'b': sb.Append('\b'); break; case 'f': sb.Append('\f'); break;
                    case 'u': sb.Append((char)Convert.ToInt32(s.Substring(i, 4), 16)); i += 4; break;
                    default: sb.Append(e); break;
                }
            }
            throw new FormatException("unterminated string");
        }

        static double Num(string s, ref int i)
        {
            int st = i;
            while (i < s.Length && "+-0123456789.eE".IndexOf(s[i]) >= 0) i++;
            if (st == i) throw new FormatException("unexpected character '" + s[i] + "' at " + i);
            return double.Parse(s.Substring(st, i - st), CultureInfo.InvariantCulture);
        }

        // helpers for reading params
        public static int Int(Dictionary<string, object> d, string k) { if (!d.ContainsKey(k) || d[k] == null) throw new KeyNotFoundException("missing argument '" + k + "'"); return Convert.ToInt32(d[k], CultureInfo.InvariantCulture); }
        public static double Num(Dictionary<string, object> d, string k, double def) { return d.ContainsKey(k) && d[k] != null ? Convert.ToDouble(d[k], CultureInfo.InvariantCulture) : def; }
        public static string Str(Dictionary<string, object> d, string k, string def) { return d.ContainsKey(k) && d[k] != null ? d[k].ToString() : def; }
        public static Dictionary<string, object> Dict(Dictionary<string, object> d, string k) { return d.ContainsKey(k) ? d[k] as Dictionary<string, object> ?? new Dictionary<string, object>() : new Dictionary<string, object>(); }
    }
}
