# Rumi — Economy System (Phase 1)

Cloudflare Workers + Hono + grammY + D1. No always-on process, no polling —
Telegram pushes updates to a webhook, the Worker handles them and returns.

## What's in here

```
src/
├── index.ts                  Worker entrypoint (Hono app + webhook route)
├── core/                     Env types, id generator
├── db/migrations/            D1 schema (SQL)
├── economy/
│   ├── config.ts             ALL tunable numbers live here
│   ├── services/              WalletService, TransactionService, RewardService, EconomyService
│   └── commands/               /balance /daily /pay /leaderboard
└── ui/                        cards.ts (message text), keyboards.ts (buttons)
```

---

## 1. Set up Termux (one time)

Open Termux and run each line:

```bash
pkg update -y && pkg upgrade -y
pkg install -y nodejs-lts git
```

We will **not use nano or vim**. The one file you must hand-edit later
(`wrangler.toml`) will be edited with a plain `cat` command below — no
terminal editor required. If you ever want a friendly editor for anything
else, install `micro` (much easier than nano/vim):

```bash
pkg install -y micro
# to edit a file:  micro somefile.ts   (Ctrl+S save, Ctrl+Q quit)
```

Check versions:

```bash
node -v
npm -v
```

---

## 2. Get the project onto your phone

You already have the `rumi-bot` project as a zip from this chat. In Termux:

```bash
cd ~
# if the zip is in your phone's Downloads folder and you've run `termux-setup-storage` once:
cp /sdcard/Download/rumi-bot.zip .
unzip rumi-bot.zip
cd rumi-bot
```

If `unzip` isn't found: `pkg install -y unzip`

Install dependencies:

```bash
npm install
```

---

## 3. Create your Telegram bot

1. In Telegram, open a chat with **@BotFather**.
2. Send `/newbot`, follow the prompts, choose a name and a username ending in `bot`.
3. BotFather gives you a **bot token** — save it somewhere safe. You'll need it below.

---

## 4. Cloudflare login

```bash
npx wrangler login
```

This opens a browser link — log in with (or create) a free Cloudflare account, then approve access. Termux will confirm once it's linked.

---

## 5. Create the D1 database

```bash
npx wrangler d1 create rumi-db
```

This prints something like:

```
[[d1_databases]]
binding = "DB"
database_name = "rumi-db"
database_id = "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
```

Copy that `database_id` value. Now write it into `wrangler.toml` **without opening any editor**:

```bash
DB_ID="paste-your-database-id-here"
sed -i "s/REPLACE_WITH_YOUR_DATABASE_ID/$DB_ID/" wrangler.toml
```

Confirm it worked:

```bash
cat wrangler.toml
```

---

## 6. Run the database migration

This creates all the tables (users, wallets, transactions, streaks):

```bash
npm run db:migrate:remote
```

(Use `db:migrate:local` instead if you want to test against a local D1 first with `npx wrangler dev`.)

---

## 7. Set your secrets

Secrets are never written into any file — they're stored securely by Cloudflare.

```bash
npx wrangler secret put TELEGRAM_BOT_TOKEN
# paste the token from BotFather when prompted

npx wrangler secret put WEBHOOK_SECRET
# type any random hard-to-guess string, e.g. a password you make up
```

Remember the `WEBHOOK_SECRET` value you chose — you'll need it in step 9.

---

## 8. Deploy

```bash
npm run deploy
```

Wrangler prints a URL like:

```
https://rumi-bot.<your-subdomain>.workers.dev
```

Copy that URL.

---

## 9. Tell Telegram where to send updates

```bash
node scripts/set-webhook.mjs "<your bot token>" "https://rumi-bot.<your-subdomain>.workers.dev" "<your WEBHOOK_SECRET>"
```

You should get back `{"ok":true,"result":true,"description":"Webhook was set"}`.

---

## 10. Test it

Open Telegram, message your bot:

```
/balance
/daily
/leaderboard
/pay @someoneelse 50
```

---

## Making changes later

- Edit any `.ts` file with `micro path/to/file.ts` (or re-create it via this chat).
- Change reward numbers in `src/economy/config.ts` only — nothing else should hard-code values.
- After any change: `npm run deploy` again. No need to reset the webhook unless the Worker URL itself changes.
- To add a new migration later: create `src/db/migrations/0002_something.sql`, then run `npm run db:migrate:remote` again.

## Costs

Everything above runs entirely on Cloudflare's **Free tier** (Workers + D1).
You won't hit a paid tier until usage is far beyond a small/medium bot's needs —
see the architecture notes from earlier in this conversation for exact limits.
