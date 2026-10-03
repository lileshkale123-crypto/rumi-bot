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


CREATE TABLE IF NOT EXISTS rob_xp_log (
    user_id BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_rob_xp_log ON rob_xp_log(user_id, created_at DESC);


CREATE TABLE IF NOT EXISTS ai_usage (
    user_id BIGINT NOT NULL,
    day DATE NOT NULL,
    count INT NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, day)
);

CREATE TABLE IF NOT EXISTS ai_memory (
    id BIGSERIAL PRIMARY KEY,
    chat_id BIGINT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ai_memory_chat ON ai_memory(chat_id, id DESC);

-- ===== Astraea features =====

CREATE TABLE IF NOT EXISTS ax_groups (
    chat_id BIGINT PRIMARY KEY,
    title TEXT
);

CREATE TABLE IF NOT EXISTS ax_connections (
    user_id BIGINT PRIMARY KEY,
    chat_id BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS ax_chat_members (
    chat_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    PRIMARY KEY (chat_id, user_id)
);

CREATE TABLE IF NOT EXISTS ax_user_stats (
    user_id BIGINT PRIMARY KEY,
    first_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
    message_count BIGINT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS ax_warns (
    chat_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    count INT NOT NULL DEFAULT 0,
    PRIMARY KEY (chat_id, user_id)
);

CREATE TABLE IF NOT EXISTS ax_settings (
    chat_id BIGINT PRIMARY KEY,
    flood_limit INT NOT NULL DEFAULT 0,
    flood_window INT NOT NULL DEFAULT 5,
    flood_mute INT NOT NULL DEFAULT 300,
    welcome_text TEXT,
    welcome_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    welcome_photo TEXT,
    goodbye_text TEXT,
    goodbye_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    goodbye_photo TEXT
);

CREATE TABLE IF NOT EXISTS ax_notes (
    chat_id BIGINT NOT NULL,
    name TEXT NOT NULL,
    content_type TEXT NOT NULL,
    text_content TEXT,
    file_id TEXT,
    created_by BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (chat_id, name)
);

CREATE TABLE IF NOT EXISTS ax_filters (
    chat_id BIGINT NOT NULL,
    trigger_text TEXT NOT NULL,
    content_type TEXT NOT NULL,
    text_content TEXT,
    file_id TEXT,
    created_by BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (chat_id, trigger_text)
);

CREATE TABLE IF NOT EXISTS ax_temp_punish (
    chat_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    ptype TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    reason TEXT,
    PRIMARY KEY (chat_id, user_id, ptype)
);

CREATE TABLE IF NOT EXISTS ax_afk (
    user_id BIGINT PRIMARY KEY,
    reason TEXT,
    start_time TIMESTAMPTZ NOT NULL DEFAULT now(),
    msg_count INT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS ax_ship_cooldowns (
    chat_id BIGINT PRIMARY KEY,
    last_time TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS ax_couple_of_day (
    chat_id BIGINT PRIMARY KEY,
    day DATE NOT NULL,
    user_a BIGINT NOT NULL,
    user_b BIGINT NOT NULL,
    compatibility INT NOT NULL
);


-- Vault, /work and /spin columns
ALTER TABLE wallets ADD COLUMN IF NOT EXISTS vault BIGINT NOT NULL DEFAULT 0 CHECK (vault >= 0);
ALTER TABLE wallets ADD COLUMN IF NOT EXISTS last_work_at TIMESTAMPTZ;
ALTER TABLE wallets ADD COLUMN IF NOT EXISTS last_spin_at TIMESTAMPTZ;

-- Mafia hall of fame
CREATE TABLE IF NOT EXISTS mafia_stats (
    user_id BIGINT PRIMARY KEY REFERENCES users(id),
    wins INT NOT NULL DEFAULT 0,
    games INT NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rob_msg_log (
    chat_id BIGINT NOT NULL,
    message_id BIGINT NOT NULL,
    robber_id BIGINT NOT NULL,
    victim_id BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (chat_id, message_id)
);

CREATE TABLE IF NOT EXISTS clans (
    id SERIAL PRIMARY KEY,
    name VARCHAR(16) NOT NULL,
    leader_id BIGINT NOT NULL,
    level INT NOT NULL DEFAULT 1,
    vault_balance BIGINT NOT NULL DEFAULT 0 CHECK (vault_balance >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_clans_name_lower ON clans (LOWER(name));

CREATE TABLE IF NOT EXISTS clan_members (
    user_id BIGINT PRIMARY KEY REFERENCES users(id),
    clan_id INT NOT NULL REFERENCES clans(id) ON DELETE CASCADE,
    role VARCHAR(10) NOT NULL DEFAULT 'member',
    joined_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_clan_members_clan ON clan_members (clan_id);
