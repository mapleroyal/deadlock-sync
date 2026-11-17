using DeadlockSync.Core;

namespace DeadlockSync;

/// <summary>Native tray status window. Only active work and expanded history occupy detail space.</summary>
internal sealed class StatusForm : Form
{
    private readonly TableLayoutPanel body = Stack();
    private readonly TableLayoutPanel accounts = Stack();
    private readonly Label notice = Caption();
    private readonly Label activity = Caption();
    private readonly PassDetails activePass = new();
    private readonly FlowLayoutPanel actions;
    private readonly Button retry = new() { Text = "Try again", AutoSize = true };
    private readonly Button sync = new() { AutoSize = true };
    private readonly ToolTip tips = new();
    private readonly Font selectedFont;
    private readonly Font disclosureFont;
    private readonly Dictionary<string, AccountRow> rows = new();
    private string[] order = [];
    private string expandedId = "";
    private bool enabledSync;
    private bool sizedByUser;
    private Size resizeStart;

    public StatusForm(string version, Icon icon, Func<string, Task> command)
    {
        Text = $"Deadlock Sync {version}";
        selectedFont = new Font(Font, FontStyle.Bold);
        disclosureFont = new Font("Segoe MDL2 Assets", Font.SizeInPoints, FontStyle.Regular, GraphicsUnit.Point);
        Icon = icon;
        AutoScaleMode = AutoScaleMode.Dpi;
        AutoScaleDimensions = new SizeF(96, 96);
        ClientSize = new Size(456, 190);
        MinimumSize = new Size(355, 150);
        StartPosition = FormStartPosition.CenterScreen;
        MaximizeBox = false;
        KeyPreview = true;
        KeyDown += (_, e) =>
        {
            if (e.KeyCode == Keys.Escape) { e.SuppressKeyPress = true; Close(); }
        };
        ResizeBegin += (_, _) => resizeStart = ClientSize;
        ResizeEnd += (_, _) => sizedByUser |= ClientSize.Height != resizeStart.Height;

        var viewport = new Panel { Dock = DockStyle.Fill, AutoScroll = true, TabStop = false };
        body.Dock = DockStyle.Top;
        body.Padding = new Padding(16, 12, 16, 4);
        body.Controls.Add(notice);
        body.Controls.Add(activity);
        body.Controls.Add(activePass);
        body.Controls.Add(accounts);
        viewport.Controls.Add(body);

        actions = new FlowLayoutPanel
        {
            Dock = DockStyle.Bottom, AutoSize = true, AutoSizeMode = AutoSizeMode.GrowAndShrink,
            FlowDirection = FlowDirection.RightToLeft, WrapContents = false, Padding = new Padding(8, 6, 12, 8)
        };
        sync.Click += async (_, _) => await command(enabledSync ? "quit" : "enable");
        retry.Click += async (_, _) => await command("retry");
        actions.Controls.Add(sync);
        actions.Controls.Add(retry);
        Controls.Add(viewport);
        Controls.Add(actions);
    }

    public void UpdateStatus(IReadOnlyList<AccountRowView> data, string message, ProgressSnapshot? unassigned,
        bool enabled, bool canRetry)
    {
        SuspendLayout();
        body.SuspendLayout();
        enabledSync = enabled;
        sync.Text = enabled ? "Quit syncing" : "Enable syncing";
        retry.Visible = canRetry;
        ShowText(notice, message);
        ShowText(activity, unassigned is null ? "" : Phase(unassigned));
        activePass.Display(unassigned, false);

        var ids = data.Select(a => a.Id).ToArray();
        bool reordered = !ids.SequenceEqual(order);
        if (reordered)
        {
            // Keep the existing controls on normal ticks so Tab focus, disclosure and scrolling survive.
            accounts.SuspendLayout();
            accounts.Controls.Clear();
            foreach (string removed in rows.Keys.Except(ids).ToArray())
            {
                rows[removed].Dispose();
                rows.Remove(removed);
            }
            if (!ids.Contains(expandedId)) expandedId = "";
        }
        foreach (var item in data)
        {
            if (!rows.TryGetValue(item.Id, out var row))
                rows.Add(item.Id, row = new AccountRow(tips, selectedFont, disclosureFont, ToggleHistory));
            if (reordered) accounts.Controls.Add(row);
            row.Display(item, data.Count > 1, expandedId == item.Id);
        }
        if (reordered) { order = ids; accounts.ResumeLayout(true); }
        accounts.Visible = data.Count != 0;
        body.ResumeLayout(true);
        ResumeLayout(true);
        FitHeight();
    }

    private void ToggleHistory(string id)
    {
        if (rows.TryGetValue(expandedId, out var previous)) previous.SetExpanded(false);
        expandedId = expandedId == id ? "" : id;
        if (expandedId.Length != 0) rows[id].SetExpanded(true);
        FitHeight();
    }

    private void FitHeight()
    {
        if (!Visible || sizedByUser || WindowState != FormWindowState.Normal) return;
        var screen = Screen.FromControl(this).WorkingArea;
        int chrome = Height - ClientSize.Height;
        int maximum = Math.Max(MinimumSize.Height - chrome, screen.Height * 2 / 3 - chrome);
        int desired = Math.Clamp(body.Height + actions.Height, MinimumSize.Height - chrome, maximum);
        if (ClientSize.Height != desired) ClientSize = new Size(ClientSize.Width, desired);
        // A row expanded near the taskbar can grow the window beyond the working area.
        if (Bottom > screen.Bottom) Top = Math.Max(screen.Top, screen.Bottom - Height);
    }

    public void PositionByTray()
    {
        var cursor = Cursor.Position;
        var area = Screen.FromPoint(cursor).WorkingArea;
        int gap = LogicalToDeviceUnits(8);
        int x = cursor.X >= area.Left + area.Width / 2 ? cursor.X - Width : cursor.X;
        int y = cursor.Y >= area.Top + area.Height / 2 ? cursor.Y - Height - gap : cursor.Y + gap;
        Location = new Point(Math.Clamp(x, area.Left, Math.Max(area.Left, area.Right - Width)),
            Math.Clamp(y, area.Top, Math.Max(area.Top, area.Bottom - Height)));
    }

    private static TableLayoutPanel Stack() => new()
    {
        ColumnCount = 1, GrowStyle = TableLayoutPanelGrowStyle.AddRows,
        AutoSize = true, AutoSizeMode = AutoSizeMode.GrowAndShrink, Dock = DockStyle.Top,
        Margin = Padding.Empty, Padding = Padding.Empty, TabStop = false,
        ColumnStyles = { new ColumnStyle(SizeType.Percent, 100) }
    };

    private static Label Caption() => new()
    {
        AutoSize = true, Dock = DockStyle.Fill, UseMnemonic = false,
        TextAlign = ContentAlignment.MiddleLeft, Margin = new Padding(0, 2, 0, 3)
    };

    private static void ShowText(Label label, string text)
    {
        label.Text = text;
        label.Visible = text.Length != 0;
    }

    internal static string LocalTime(long utc) => DateTimeOffset.FromUnixTimeSeconds(utc).ToLocalTime().ToString("g");

    private static string Phase(ProgressSnapshot p) => p.Phase switch
    {
        "reading" => "Reading the current game",
        "watching" => "Watching for new matches",
        "waiting" => "Waiting for match data",
        "recovering" => "Recovering missing matches",
        "complete" => "History processed",
        "limited" => "Steam cooldown",
        "failed" => p.Mode == "live" ? "Live sync needs attention" : "History needs attention",
        _ => p.Mode == "live" ? "Watching for new matches" : "Checking match history"
    };

    private sealed class AccountRow : TableLayoutPanel
    {
        private readonly Label name = new() { AutoEllipsis = true, Dock = DockStyle.Fill, UseMnemonic = false, TextAlign = ContentAlignment.MiddleLeft };
        private readonly Label status = Caption();
        private readonly Label current = Caption();
        private readonly Label note = Caption();
        private readonly Button more = new()
        {
            AutoSize = false, FlatStyle = FlatStyle.Flat, UseVisualStyleBackColor = true, Dock = DockStyle.Fill,
            Margin = Padding.Empty, Padding = Padding.Empty, AccessibleRole = AccessibleRole.PushButton
        };
        private readonly ToolTip tips;
        private readonly Font selectedFont;
        private AccountRowView? item;
        private PassDetails? details;

        public AccountRow(ToolTip tips, Font selectedFont, Font disclosureFont, Action<string> toggle)
        {
            this.tips = tips;
            ColumnCount = 1;
            ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
            AutoSize = true;
            AutoSizeMode = AutoSizeMode.GrowAndShrink;
            Dock = DockStyle.Top;
            Margin = new Padding(0, 2, 0, 6);
            this.selectedFont = selectedFont;
            var header = new TableLayoutPanel
            {
                ColumnCount = 4, RowCount = 1, AutoSize = true, AutoSizeMode = AutoSizeMode.GrowAndShrink,
                Dock = DockStyle.Fill, Margin = Padding.Empty, MinimumSize = new Size(0, Font.Height + 12)
            };
            // Measure the name column against the whole row, independent of trailing controls.
            var nameColumn = new ColumnStyle(SizeType.Absolute, 124);
            header.ColumnStyles.Add(nameColumn);
            header.ClientSizeChanged += (_, _) => nameColumn.Width = header.ClientSize.Width * 0.30f;
            header.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
            header.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
            header.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
            name.Margin = new Padding(0, 0, 8, 0);
            current.Text = "Current";
            current.ForeColor = SystemColors.HotTrack;
            current.Font = selectedFont;
            current.Margin = new Padding(6, 0, 0, 0);
            more.Font = disclosureFont;
            more.FlatAppearance.BorderSize = 0;
            more.Size = new Size(Font.Height + 12, Font.Height + 12);
            more.Click += (_, _) => { if (item is not null) toggle(item.Id); };
            header.Controls.Add(name, 0, 0);
            header.Controls.Add(status, 1, 0);
            header.Controls.Add(current, 2, 0);
            header.Controls.Add(more, 3, 0);
            Controls.Add(header, 0, 0);
            Controls.Add(note, 0, 1);
        }

        public void Display(AccountRowView model, bool multiple, bool expanded)
        {
            item = model;
            name.Text = model.Name;
            name.Font = model.IsCurrent ? selectedFont : Font;
            tips.SetToolTip(name, model.Name);
            current.Visible = multiple && model.IsCurrent;
            status.Text = model.Phase switch
            {
                "working" or "live" => model.ActiveProgress is { } p ? Phase(p)
                    : model.Phase == "live" ? "Watching for new matches" : "Checking match history",
                "caught_up" => "Caught up",
                "cooldown" => "Steam cooldown",
                "auth" => "Check sign-in in Steam",
                "choose_account" => "Select in Steam to retry",
                "attention" => model.Reason == "state_store" ? "Check recovery storage" : "History needs attention",
                _ => model.Reason switch
                {
                    "account_in_use" => "Steam account in use", "transient" => "Waiting to retry", _ => "Waiting"
                }
            };
            var lines = new List<string>();
            if (model.NextAtUtc > 0) lines.Add("Next attempt " + LocalTime(model.NextAtUtc));
            if (model.Skipped > 0) lines.Add($"{model.Skipped:N0} older {(model.Skipped == 1 ? "match" : "matches")} unavailable");
            ShowText(note, string.Join(Environment.NewLine, lines));
            SetExpanded(expanded);
        }

        public void SetExpanded(bool expanded)
        {
            if (item is null) return;
            more.Visible = item.LastPass is not null;
            expanded &= item.LastPass is not null;
            more.Text = expanded ? "\uE70D" : "\uE76C"; // Windows ChevronDown / ChevronRight
            more.AccessibleName = (expanded ? "Hide" : "Show") + " last backfill for " + item.Name;
            tips.SetToolTip(more, expanded ? "Hide last backfill" : "Show last backfill");
            // Live reading can coexist with a prior backfill. Opening it must never replace current live status.
            var pass = expanded ? item.LastPass : item.ActiveProgress;
            if (details is null && pass is not null) { details = new PassDetails(); Controls.Add(details, 0, 2); }
            details?.Display(pass, expanded);
        }

    }

    private sealed class PassDetails : TableLayoutPanel
    {
        private readonly Label heading = Caption();
        private readonly Label count = Caption();
        private readonly ProgressBar bar = new() { Dock = DockStyle.Fill, Height = 8, Margin = new Padding(0, 2, 0, 5), AccessibleName = "Missing matches processed" };
        private readonly Label stats = Caption();
        private readonly Label updated = Caption();

        public PassDetails()
        {
            ColumnCount = 1;
            ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
            AutoSize = true;
            AutoSizeMode = AutoSizeMode.GrowAndShrink;
            Dock = DockStyle.Top;
            Margin = new Padding(0, 0, 0, 4);
            foreach (Control control in new Control[] { heading, count, bar, stats, updated }) Controls.Add(control);
        }

        public void Display(ProgressSnapshot? pass, bool historical)
        {
            Visible = pass is not null;
            if (pass is null) return;
            ShowText(heading, historical ? "Last backfill" : "");
            ShowText(count, pass.Completed.HasValue && pass.Total.HasValue
                ? $"{pass.Completed:N0} of {pass.Total:N0} missing {(pass.Total == 1 ? "match" : "matches")} processed" : "");
            bar.Visible = !historical && pass.Total is > 0 && pass.Completed >= 0;
            if (pass.Total is > 0 && pass.Completed is int completed)
            {
                bar.Maximum = pass.Total.Value;
                bar.Value = Math.Clamp(completed, 0, bar.Maximum);
            }
            var counts = new List<string>();
            if (pass.Uploaded is int uploaded) counts.Add($"{uploaded:N0} uploaded");
            else if (pass.Completed > 0) counts.Add("Upload count unknown");
            if (pass.Failed > 0) counts.Add($"{pass.Failed:N0} failed");
            if (pass.History is int history) counts.Add($"{history:N0} in history");
            ShowText(stats, string.Join("   ·   ", counts));
            ShowText(updated, pass.UpdatedAtUtc is long stamp ? "Updated " + LocalTime(stamp) : "");
        }
    }

    protected override void Dispose(bool disposing)
    {
        base.Dispose(disposing);
        if (disposing) { tips.Dispose(); selectedFont.Dispose(); disclosureFont.Dispose(); }
    }
}
