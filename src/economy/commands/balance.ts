import type { Context } from "grammy";
import type { Env } from "../../core/types";
import { WalletService } from "../services/WalletService";
import { balanceCard, errorCard } from "../../ui/cards";
import { walletKeyboard } from "../../ui/keyboards";

export async function handleBalance(ctx: Context, env: Env): Promise<void> {
  if (!ctx.from) return;
  const userId = String(ctx.from.id);
  const username = ctx.from.username ?? ctx.from.first_name ?? "Traveler";

  const wallets = new WalletService(env.DB);
  await wallets.ensureUserAndWallet(userId, ctx.from.username);
  const wallet = await wallets.getWallet(userId);

  if (!wallet) {
    await ctx.reply(errorCard("Couldn't load your wallet right now. Please try again."));
    return;
  }

  const text = balanceCard(username, wallet.cash, wallet.gems, wallet.level, wallet.xp);
  const keyboard = walletKeyboard();

  // If triggered via the "Refresh" button, edit in place instead of spamming a new message.
  if (ctx.callbackQuery) {
    try {
      await ctx.editMessageText(text, { reply_markup: keyboard });
    } catch {
      // message content identical / too old to edit — ignore silently
    }
    await ctx.answerCallbackQuery();
    return;
  }

  await ctx.reply(text, { reply_markup: keyboard });
}
