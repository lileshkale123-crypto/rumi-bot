import type { Context } from "grammy";
import type { Env } from "../../core/types";
import { WalletService } from "../services/WalletService";
import { EconomyService } from "../services/EconomyService";
import { paymentSuccessCard, errorCard } from "../../ui/cards";

export async function handlePay(ctx: Context, env: Env): Promise<void> {
  if (!ctx.from) return;
  const userId = String(ctx.from.id);

  const wallets = new WalletService(env.DB);
  await wallets.ensureUserAndWallet(userId, ctx.from.username);

  const raw = ctx.match?.toString().trim() ?? "";
  const args = raw.split(/\s+/).filter(Boolean);

  if (args.length < 2) {
    await ctx.reply(errorCard("Usage: /pay @username amount"));
    return;
  }

  const targetUsername = args[0].replace(/^@/, "");
  const amount = parseInt(args[1], 10);

  if (!targetUsername || Number.isNaN(amount)) {
    await ctx.reply(errorCard("Usage: /pay @username amount"));
    return;
  }

  const economy = new EconomyService(env.DB);
  const result = await economy.pay(userId, targetUsername, amount);

  switch (result.status) {
    case "ok":
      await ctx.reply(paymentSuccessCard(amount, targetUsername, result.newSenderBalance));
      return;
    case "self_transfer":
      await ctx.reply(errorCard("You can't pay yourself."));
      return;
    case "invalid_amount":
      await ctx.reply(errorCard("Enter a valid positive whole amount."));
      return;
    case "below_minimum":
      await ctx.reply(errorCard("That's below the minimum transfer amount."));
      return;
    case "above_maximum":
      await ctx.reply(errorCard("That exceeds the maximum transfer amount."));
      return;
    case "insufficient_funds":
      await ctx.reply(errorCard("You don't have enough balance for this transfer."));
      return;
    case "daily_limit_exceeded":
      await ctx.reply(errorCard("You've hit your daily transfer limit."));
      return;
    case "receiver_not_found":
      await ctx.reply(errorCard("That user hasn't used Rumi yet."));
      return;
    default:
      await ctx.reply(errorCard("Something went wrong. Please try again."));
  }
}
