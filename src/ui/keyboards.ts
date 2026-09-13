import { InlineKeyboard } from "grammy";

export function walletKeyboard(): InlineKeyboard {
  return new InlineKeyboard()
    .text("💰 Daily", "daily")
    .text("🏆 Rank", "leaderboard:cash:1")
    .row()
    .text("🔄 Refresh", "balance:refresh");
}

export function leaderboardKeyboard(category: "cash" | "xp" | "gems", page: number, totalPages: number): InlineKeyboard {
  const kb = new InlineKeyboard()
    .text("💵 Richest", "leaderboard:cash:1")
    .text("⭐ XP", "leaderboard:xp:1")
    .text("💎 Gems", "leaderboard:gems:1")
    .row();

  if (page > 1) kb.text("◀", `leaderboard:${category}:${page - 1}`);
  kb.text(`${page}/${totalPages}`, "noop");
  if (page < totalPages) kb.text("▶", `leaderboard:${category}:${page + 1}`);

  return kb;
}
