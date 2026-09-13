import { Hono } from "hono";
import { Bot, webhookCallback } from "grammy";
import type { Env } from "./core/types";
import { handleBalance } from "./economy/commands/balance";
import { handleDaily } from "./economy/commands/daily";
import { handlePay } from "./economy/commands/pay";
import { handleLeaderboard, handleLeaderboardCallback } from "./economy/commands/leaderboard";

const app = new Hono<{ Bindings: Env }>();

app.get("/", (c) => c.text("Rumi is alive 🌙"));

// Telegram calls this URL for every update. The secret in the path stops
// randoms from POSTing fake updates to your bot.
app.post("/webhook/:secret", async (c) => {
  if (c.req.param("secret") !== c.env.WEBHOOK_SECRET) {
    return c.text("Not found", 404);
  }

  const bot = new Bot(c.env.TELEGRAM_BOT_TOKEN);

  bot.command("balance", (ctx) => handleBalance(ctx, c.env));
  bot.command("daily", (ctx) => handleDaily(ctx, c.env));
  bot.command("pay", (ctx) => handlePay(ctx, c.env));
  bot.command("leaderboard", (ctx) => handleLeaderboard(ctx, c.env));

  bot.callbackQuery(/^leaderboard:/, (ctx) => handleLeaderboardCallback(ctx, c.env));
  bot.callbackQuery("daily", (ctx) => handleDaily(ctx, c.env));
  bot.callbackQuery("balance:refresh", (ctx) => handleBalance(ctx, c.env));
  bot.callbackQuery("noop", (ctx) => ctx.answerCallbackQuery());

  bot.catch((err) => {
    console.error("Bot error:", err);
  });

  const handleUpdate = webhookCallback(bot, "hono");
  return handleUpdate(c);
});

export default app;
