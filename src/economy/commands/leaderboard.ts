import type { Context } from "grammy";
import type { Env } from "../../core/types";
import { EconomyService } from "../services/EconomyService";
import { leaderboardCard } from "../../ui/cards";
import { leaderboardKeyboard } from "../../ui/keyboards";
import { ECONOMY_CONFIG } from "../config";

type Category = "cash" | "xp" | "gems";

const CATEGORY_META: Record<Category, { title: string; emoji: string; isCurrency: boolean }> = {
  cash: { title: "Richest", emoji: "💵", isCurrency: true },
  xp: { title: "Top XP", emoji: "⭐", isCurrency: false },
  gems: { title: "Most Gems", emoji: "💎", isCurrency: false },
};

async function renderLeaderboard(env: Env, category: Category, page: number) {
  const economy = new EconomyService(env.DB);
  const pageSize = ECONOMY_CONFIG.LEADERBOARD_PAGE_SIZE;
  const { results, total } = await economy.getLeaderboard(category, page, pageSize);
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const meta = CATEGORY_META[category];

  const entries = (results as { username: string | null; value: number }[]).map((r) => ({
    username: r.username ?? "unknown",
    value: r.value,
  }));

  const text = leaderboardCard(meta.title, meta.emoji, entries, page, totalPages, meta.isCurrency);
  const keyboard = leaderboardKeyboard(category, page, totalPages);
  return { text, keyboard };
}

export async function handleLeaderboard(ctx: Context, env: Env): Promise<void> {
  const { text, keyboard } = await renderLeaderboard(env, "cash", 1);
  await ctx.reply(text, { reply_markup: keyboard });
}

export async function handleLeaderboardCallback(ctx: Context, env: Env): Promise<void> {
  const data = ctx.callbackQuery?.data ?? "";
  const [, categoryRaw, pageRaw] = data.split(":");

  if (categoryRaw !== "cash" && categoryRaw !== "xp" && categoryRaw !== "gems") {
    await ctx.answerCallbackQuery();
    return;
  }

  const page = Math.max(1, parseInt(pageRaw, 10) || 1);
  const { text, keyboard } = await renderLeaderboard(env, categoryRaw, page);

  try {
    await ctx.editMessageText(text, { reply_markup: keyboard });
  } catch {
    // identical content or message too old to edit — ignore
  }
  await ctx.answerCallbackQuery();
}
