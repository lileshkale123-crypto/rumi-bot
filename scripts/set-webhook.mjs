// Usage: node scripts/set-webhook.mjs <bot_token> <worker_url> <webhook_secret>
// Example:
//   node scripts/set-webhook.mjs 123456:ABC-token https://rumi-bot.you.workers.dev mySecret123

const [, , token, workerUrl, secret] = process.argv;

if (!token || !workerUrl || !secret) {
  console.error("Usage: node scripts/set-webhook.mjs <bot_token> <worker_url> <webhook_secret>");
  process.exit(1);
}

const url = `https://api.telegram.org/bot${token}/setWebhook`;
const webhookUrl = `${workerUrl.replace(/\/$/, "")}/webhook/${secret}`;

const res = await fetch(url, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ url: webhookUrl }),
});

const data = await res.json();
console.log(data);
