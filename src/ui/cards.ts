import { ECONOMY_CONFIG } from "../economy/config";

function formatNumber(n: number): string {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M";
  if (n >= 1_000) return (n / 1_000).toFixed(1) + "K";
  return n.toString();
}

export function balanceCard(username: string, cash: number, gems: number, level: number, xp: number): string {
  return [
    "╭────── 🌙 RUMI WALLET ──────╮",
    "",
    `        👤 ${username}`,
    "",
    `       ${ECONOMY_CONFIG.CURRENCY_SYMBOL}${formatNumber(cash)}`,
    `       💎 ${formatNumber(gems)} Gems`,
    `       ⭐ Level ${level}`,
    `       ✨ ${formatNumber(xp)} XP`,
    "",
    "╰─────────────────────────────╯",
  ].join("\n");
}

export function dailyClaimedCard(
  cash: number,
  gems: number,
  luckyBonus: number,
  streak: number,
  milestoneHit: number | null
): string {
  const lines = ["╭────── 🌙 RUMI DAILY ──────╮", "", "      🎁 Daily Chest", "", `   ${ECONOMY_CONFIG.CURRENCY_SYMBOL}+${formatNumber(cash)}`];
  if (gems > 0) lines.push(`   💎 +${gems} Gem${gems > 1 ? "s" : ""}`);
  lines.push(`   🔥 ${streak} Day Streak`);
  if (luckyBonus > 0) lines.push("", `   ✨ Lucky Bonus: +${ECONOMY_CONFIG.CURRENCY_SYMBOL}${formatNumber(luckyBonus)}`);
  if (milestoneHit) lines.push("", `   🏅 Milestone reached: Day ${milestoneHit}!`);
  lines.push("", "╰────────────────────────────╯");
  return lines.join("\n");
}

export function dailyCooldownCard(nextClaimAt: number): string {
  const remainingMs = Math.max(0, nextClaimAt - Date.now());
  const hours = Math.floor(remainingMs / 3600000);
  const minutes = Math.floor((remainingMs % 3600000) / 60000);
  return [
    "╭────── 🌙 RUMI DAILY ──────╮",
    "",
    "   ⏳ Already claimed today!",
    `   Come back in ${hours}h ${minutes}m`,
    "",
    "╰────────────────────────────╯",
  ].join("\n");
}

export function paymentSuccessCard(amount: number, toUsername: string, newBalance: number): string {
  return [
    "🌙 Payment Complete",
    "",
    `💸 Sent: ${ECONOMY_CONFIG.CURRENCY_SYMBOL}${formatNumber(amount)}`,
    `👤 To: @${toUsername}`,
    "",
    `💳 Your balance: ${ECONOMY_CONFIG.CURRENCY_SYMBOL}${formatNumber(newBalance)}`,
  ].join("\n");
}

export function errorCard(message: string): string {
  return `⚠️ ${message}`;
}

export function leaderboardCard(
  title: string,
  emoji: string,
  entries: { username: string; value: number }[],
  page: number,
  totalPages: number,
  isCurrency: boolean
): string {
  const medals = ["🥇", "🥈", "🥉"];
  const lines = ["╭────── 🏆 RUMI RANKINGS ──────╮", "", `${emoji} ${title}`, ""];
  entries.forEach((e, i) => {
    const rank = (page - 1) * ECONOMY_CONFIG.LEADERBOARD_PAGE_SIZE + i + 1;
    const medal = rank <= 3 ? medals[rank - 1] : `${rank}.`;
    const value = isCurrency ? `${ECONOMY_CONFIG.CURRENCY_SYMBOL}${formatNumber(e.value)}` : formatNumber(e.value);
    lines.push(`${medal} @${e.username ?? "unknown"} — ${value}`);
  });
  if (entries.length === 0) lines.push("Nobody here yet — be the first!");
  lines.push("", `[ Page ${page}/${totalPages} ]`, "", "╰──────────────────────────────╯");
  return lines.join("\n");
}
