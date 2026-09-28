-- Rumi Economy — Postgres schema (Python rebuild)

CREATE TABLE IF NOT EXISTS users (
    id BIGINT PRIMARY KEY,              -- Telegram user id
    username TEXT,
    first_name TEXT,                    -- always present on Telegram; used for display
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS wallets (
    user_id BIGINT PRIMARY KEY REFERENCES users(id),
    cash BIGINT NOT NULL DEFAULT 0 CHECK (cash >= 0),
    gems BIGINT NOT NULL DEFAULT 0 CHECK (gems >= 0),
    xp BIGINT NOT NULL DEFAULT 0 CHECK (xp >= 0),
    level INT NOT NULL DEFAULT 1 CHECK (level >= 1),
    daily_transferred BIGINT NOT NULL DEFAULT 0,
    daily_transferred_date DATE,
    shield_until TIMESTAMPTZ,           -- /ward protection expiry
    wins INT NOT NULL DEFAULT 0,        -- successful robs
    losses INT NOT NULL DEFAULT 0,      -- failed rob attempts
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS transactions (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    type TEXT NOT NULL,
    currency TEXT NOT NULL,
    amount BIGINT NOT NULL,
    balance_before BIGINT NOT NULL,
    balance_after BIGINT NOT NULL,
    related_user_id BIGINT,
    source TEXT NOT NULL,
    metadata JSONB,
    idempotency_key TEXT UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tx_user ON transactions(user_id);
CREATE INDEX IF NOT EXISTS idx_tx_created ON transactions(created_at);

CREATE TABLE IF NOT EXISTS streaks (
    user_id BIGINT PRIMARY KEY REFERENCES users(id),
    current_streak INT NOT NULL DEFAULT 0,
    longest_streak INT NOT NULL DEFAULT 0,
    last_claim_date DATE,
    milestones_claimed JSONB NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS rob_cooldowns (
    user_id BIGINT PRIMARY KEY,
    last_rob_at TIMESTAMPTZ
);

-- Leaderboard-friendly indexes
CREATE INDEX IF NOT EXISTS idx_wallets_cash ON wallets(cash DESC);
CREATE INDEX IF NOT EXISTS idx_wallets_xp ON wallets(xp DESC);
CREATE INDEX IF NOT EXISTS idx_wallets_gems ON wallets(gems DESC);

CREATE TABLE IF NOT EXISTS bluff_games (
    id TEXT PRIMARY KEY,
    chat_id BIGINT NOT NULL,
    wager BIGINT NOT NULL,
    status TEXT NOT NULL,
    player_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
