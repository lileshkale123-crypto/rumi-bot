-- Rumi Economy — initial schema

CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,              -- Telegram user id, stored as text
  username TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS wallets (
  user_id TEXT PRIMARY KEY REFERENCES users(id),
  cash INTEGER NOT NULL DEFAULT 0 CHECK (cash >= 0),
  gems INTEGER NOT NULL DEFAULT 0 CHECK (gems >= 0),
  xp INTEGER NOT NULL DEFAULT 0 CHECK (xp >= 0),
  level INTEGER NOT NULL DEFAULT 1 CHECK (level >= 1),
  daily_transferred INTEGER NOT NULL DEFAULT 0,
  daily_transferred_date TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS transactions (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  type TEXT NOT NULL,
  currency TEXT NOT NULL,
  amount INTEGER NOT NULL,
  balance_before INTEGER NOT NULL,
  balance_after INTEGER NOT NULL,
  related_user_id TEXT,
  source TEXT NOT NULL,
  metadata TEXT,
  idempotency_key TEXT UNIQUE,
  created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tx_user ON transactions(user_id);
CREATE INDEX IF NOT EXISTS idx_tx_created ON transactions(created_at);

CREATE TABLE IF NOT EXISTS streaks (
  user_id TEXT PRIMARY KEY REFERENCES users(id),
  current_streak INTEGER NOT NULL DEFAULT 0,
  longest_streak INTEGER NOT NULL DEFAULT 0,
  last_claim_at INTEGER,
  milestones_claimed TEXT NOT NULL DEFAULT '[]'
);

-- Leaderboard-friendly indexes
CREATE INDEX IF NOT EXISTS idx_wallets_cash ON wallets(cash DESC);
CREATE INDEX IF NOT EXISTS idx_wallets_xp ON wallets(xp DESC);
CREATE INDEX IF NOT EXISTS idx_wallets_gems ON wallets(gems DESC);
