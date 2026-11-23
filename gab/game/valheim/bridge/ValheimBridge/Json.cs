// Tiny JSON writer (no external deps on .NET 3.5).
using System;
using System.Collections;
using System.Globalization;
using System.Text;

namespace ValheimBridge
{
    public static class Json
    {
        public static string Obj(params object[] kv)
        {
            var d = new Hashtable();
            var sb = new StringBuilder("{");
            for (int i = 0; i + 1 < kv.Length; i += 2)
            {
                if (i > 0) sb.Append(',');
                sb.Append(Str((string)kv[i])).Append(':').Append(Val(kv[i + 1]));
            }
            return sb.Append('}').ToString();
        }

        public static string Val(object v)
        {
            if (v == null) return "null";
            if (v is Raw) return ((Raw)v).Text;
            if (v is string) return Str((string)v);
            if (v is bool) return (bool)v ? "true" : "false";
            if (v is float) return ((float)v).ToString("0.###", CultureInfo.InvariantCulture);
            if (v is double) return ((double)v).ToString("0.###", CultureInfo.InvariantCulture);
            if (v is int || v is long || v is short || v is uint) return Convert.ToString(v, CultureInfo.InvariantCulture);
            if (v is IDictionary)
            {
                var sb = new StringBuilder("{"); bool first = true;
                foreach (DictionaryEntry e in (IDictionary)v) { if (!first) sb.Append(','); first = false; sb.Append(Str(e.Key.ToString())).Append(':').Append(Val(e.Value)); }
                return sb.Append('}').ToString();
            }
            if (v is IEnumerable)
            {
                var sb = new StringBuilder("["); bool first = true;
                foreach (var x in (IEnumerable)v) { if (!first) sb.Append(','); first = false; sb.Append(Val(x)); }
                return sb.Append(']').ToString();
            }
            return Str(v.ToString());
        }

        public static string Str(string s)
        {
            var sb = new StringBuilder("\"");
            foreach (char c in s)
            {
                switch (c)
                {
                    case '"': sb.Append("\\\""); break;
                    case '\\': sb.Append("\\\\"); break;
                    case '\n': sb.Append("\\n"); break;
                    case '\r': sb.Append("\\r"); break;
                    case '\t': sb.Append("\\t"); break;
                    default: if (c < 0x20) sb.Append("\\u").Append(((int)c).ToString("x4")); else sb.Append(c); break;
                }
            }
            return sb.Append('"').ToString();
        }

        public class Raw { public string Text; public Raw(string t) { Text = t; } }
    }
}
