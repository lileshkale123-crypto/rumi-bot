import type { Context } from "grammy";
import type { Env } from "../../core/types";
import { WalletService } from "../services/WalletService";
import { RewardService } from "../services/RewardService";
import { dailyClaimedCard, dailyCooldownCard, errorCard } from "../../ui/cards";

export async function handleDaily(ctx: Context, env: Env): Promise<void> {
  if (!ctx.from) return;
  const userId = String(ctx.from.id);

  const wallets = new WalletService(env.DB);
  await wallets.ensureUserAndWallet(userId, ctx.from.username);

  const rewards = new RewardService(env.DB);
  const result = await rewards.claimDaily(userId);

  if (ctx.callbackQuery) {
    await ctx.answerCallbackQuery();
  }

  if (result.status === "cooldown") {
    await ctx.reply(dailyCooldownCard(result.nextClaimAt!));
    return;
  }
  if (result.status === "already_claimed_race") {
    await ctx.reply(errorCard("You already claimed your daily reward just now."));
    return;
  }

  await ctx.reply(
    dailyClaimedCard(result.cashReward!, result.gemReward!, result.luckyBonus!, result.newStreak!, result.milestoneHit ?? null)
  );
}
