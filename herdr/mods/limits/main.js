import { View, div } from "gpui-kit";
import { h_flex } from "gpui-base";
import { theme, home, readTextFile } from "herdr";

// ponytail: 5 min poll, Claude's usage endpoint answers 429 if hit too often. Click the bar to refresh now.
const POLL_MS = 5 * 60 * 1000;

async function readJson(path) {
  const text = await readTextFile(path);
  return text ? JSON.parse(text) : null;
}

// Tokens are re-read each poll: Claude Code and Codex refresh them on their own.
async function claude() {
  const creds = await readJson(`${home()}/.claude/.credentials.json`);
  const token = creds?.claudeAiOauth?.accessToken;
  if (!token) throw new Error("not logged in");
  const r = await fetch("https://api.anthropic.com/api/oauth/usage", {
    headers: { Authorization: `Bearer ${token}`, "anthropic-beta": "oauth-2025-04-20" },
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  const u = await r.json();
  const win = (w) => w && { pct: w.utilization, reset: Date.parse(w.resets_at) };
  return [["5h", win(u.five_hour)], ["7d", win(u.seven_day)]];
}

async function codex() {
  const auth = await readJson(`${home()}/.codex/auth.json`);
  const t = auth?.tokens;
  if (!t?.access_token) throw new Error("not logged in");
  const r = await fetch("https://chatgpt.com/backend-api/wham/usage", {
    headers: { Authorization: `Bearer ${t.access_token}`, "ChatGPT-Account-Id": t.account_id ?? "", "User-Agent": "codex-cli" },
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  const rl = (await r.json()).rate_limit ?? {};
  const win = (w) => w && { pct: w.used_percent, reset: w.reset_at * 1000 };
  return [["5h", win(rl.primary_window)], ["7d", win(rl.secondary_window)]];
}

const DAYS = ["nd", "pn", "wt", "śr", "cz", "pt", "sb"];

// Reset as a local clock time; another day gets its weekday in front.
function resetAt(ms) {
  const d = new Date(ms);
  const hm = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  return d.toDateString() === new Date().toDateString() ? hm : `${DAYS[d.getDay()]} ${hm}`;
}

export default class Limits extends View {
  init(_props, cx) {
    this.data = {};
    cx.timer.every(POLL_MS, (cx) => this.refresh(cx));
    this.refresh(cx);
  }

  refresh(cx) {
    for (const [name, get] of [["Claude", claude], ["Codex", codex]]) {
      cx.spawn(async (cx) => {
        try {
          this.data[name] = { windows: await get() };
        } catch (e) {
          // Keep the last good numbers, just flag the error.
          this.data[name] = { ...this.data[name], error: String(e?.message ?? e) };
        }
        cx.notify();
      });
    }
  }

  meter(t, label, w) {
    const color = w.pct >= 90 ? t.blocked : w.pct >= 70 ? t.ansi3 : t.accent;
    return h_flex().gap(5).items_center()
      .child(div().text_color(t.dim).child(label))
      .child(div().w(40).h(5).rounded(3).bg(t.raised)
        .child(div().h_full().rounded(3).bg(color).w(Math.min(40, Math.max(0, w.pct) * 0.4))))
      .child(div().text_color(t.foreground).child(`${Math.round(w.pct)}%`))
      .child(div().text_color(t.dim).child(resetAt(w.reset)));
  }

  provider(t, name) {
    const d = this.data[name] ?? {};
    return h_flex().gap(12).items_center()
      .child(div().font_semibold().text_color(t.muted).child(name))
      .children((d.windows ?? []).filter(([, w]) => w).map(([label, w]) => this.meter(t, label, w)))
      .when(!d.windows && !d.error, (el) => el.child(div().text_color(t.dim).child("…")))
      .when(Boolean(d.error), (el) => el.child(div().text_color(t.blocked).child(d.error)));
  }

  render(cx) {
    const t = theme();
    return h_flex().id("limits").size_full().px(14).gap(24).items_center().justify_end()
      .bg(t.chrome).border_t(1).border_color(t.border).text_size(11.8)
      .on_click((_e, cx) => this.refresh(cx))
      .child(this.provider(t, "Claude"))
      .child(div().w(1).h(12).bg(t.border))
      .child(this.provider(t, "Codex"));
  }
}
