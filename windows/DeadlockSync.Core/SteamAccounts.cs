using System.Globalization;
using System.Text;

namespace DeadlockSync.Core;

public sealed record SteamAccount(string Id, string Name);
public sealed record SteamAccounts(IReadOnlyList<SteamAccount> Accounts, string CurrentId)
{
    public static SteamAccounts Empty { get; } = new([], "");
    public IReadOnlyCollection<string> Ids => Accounts.Select(x => x.Id).ToArray();
    public string Label(string id) => Accounts.FirstOrDefault(x => x.Id == id)?.Name ?? (id.Length == 0 ? "None" : "Steam account " + id);

    // loginusers.vdf contains public account metadata, never refresh tokens/passwords.
    public static SteamAccounts Parse(string text)
    {
        if (text.Length > 1_048_576) throw new InvalidDataException("Steam account metadata is too large.");
        var parser = new Vdf(text);
        var root = parser.Object(false, 0);
        if (!root.TryGetValue("users", out var usersObject) || usersObject is not Dictionary<string, object> users)
            throw new InvalidDataException("Steam account metadata has no users object.");
        var accounts = new List<SteamAccount>(); var current = new List<string>(); var autoLogin = new List<string>();
        foreach (var (key, value) in users)
        {
            if (!ulong.TryParse(key, NumberStyles.None, CultureInfo.InvariantCulture, out ulong steamId)
                || steamId <= 76561197960265728UL || steamId - 76561197960265728UL > uint.MaxValue
                || value is not Dictionary<string, object> fields) continue;
            string id = (steamId - 76561197960265728UL).ToString(CultureInfo.InvariantCulture);
            string name = fields.GetValueOrDefault("PersonaName") as string ?? fields.GetValueOrDefault("AccountName") as string ?? "Steam account " + id;
            name = new string(name.Where(c => !char.IsControl(c)).Take(80).ToArray()).Trim();
            if (name.Length == 0) name = "Steam account " + id;
            accounts.Add(new(id, name));
            if (fields.GetValueOrDefault("MostRecent") as string == "1") current.Add(id);
            if (fields.GetValueOrDefault("AutoLogin") as string == "1") autoLogin.Add(id);
        }
        if (accounts.Count > AccountPolicy.AccountLimit || accounts.Select(x => x.Id).Distinct().Count() != accounts.Count)
            throw new InvalidDataException("Steam account metadata contains too many or duplicate accounts.");
        return new(accounts, current.Count == 1 ? current[0] : autoLogin.Count == 1 ? autoLogin[0] : "");
    }

    private sealed class Vdf(string text)
    {
        private int position;
        private string? Token()
        {
            while (position < text.Length)
            {
                if (char.IsWhiteSpace(text[position]) || text[position] == '\uFEFF') { position++; continue; }
                if (text[position] == '/' && position + 1 < text.Length && text[position + 1] == '/')
                { while (position < text.Length && text[position] != '\n') position++; continue; }
                break;
            }
            if (position == text.Length) return null;
            char first = text[position++];
            if (first is '{' or '}') return "\u0001" + first;
            if (first != '"') throw new InvalidDataException("Steam account metadata is malformed.");
            var value = new StringBuilder();
            while (position < text.Length)
            {
                char c = text[position++];
                if (c == '"') return value.ToString();
                if (c == '\\')
                {
                    if (position == text.Length) break;
                    char next = text[position++];
                    if (next is '"' or '\\') c = next;
                    else { value.Append('\\'); c = next; }
                }
                value.Append(c);
                if (value.Length > 16_384) throw new InvalidDataException("Steam account metadata value is too large.");
            }
            throw new InvalidDataException("Steam account metadata is incomplete.");
        }
        public Dictionary<string, object> Object(bool braced, int depth)
        {
            if (depth > 8) throw new InvalidDataException("Steam account metadata is too deeply nested.");
            var result = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase);
            while (true)
            {
                string? key = Token();
                if (key is null && !braced || key == "\u0001}" && braced) return result;
                if (key is null or "\u0001{" or "\u0001}") throw new InvalidDataException("Steam account metadata is incomplete.");
                string? token = Token();
                if (token is null or "\u0001}") throw new InvalidDataException("Steam account metadata is incomplete.");
                object value = token == "\u0001{" ? Object(true, depth + 1) : token;
                if (!result.TryAdd(key, value) || result.Count > 512)
                    throw new InvalidDataException("Steam account metadata has duplicate or too many fields.");
            }
        }
    }
}
