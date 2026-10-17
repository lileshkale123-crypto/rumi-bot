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

CREATE TABLE IF NOT EXISTS ax_warn_settings (
    chat_id BIGINT PRIMARY KEY,
    warn_limit INT NOT NULL DEFAULT 3,
    warn_mode TEXT NOT NULL DEFAULT 'mute',
    warn_mode_secs INT NOT NULL DEFAULT 86400,
    warn_expire_secs INT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS ax_warn_log (
    id BIGSERIAL PRIMARY KEY,
    chat_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ax_warn_log_idx ON ax_warn_log (chat_id, user_id);

CREATE TABLE IF NOT EXISTS ax_approved (
    chat_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    PRIMARY KEY (chat_id, user_id)
);

CREATE TABLE IF NOT EXISTS ax_locks (
    chat_id BIGINT NOT NULL,
    lock_type TEXT NOT NULL,
    action TEXT,
    reason TEXT,
    PRIMARY KEY (chat_id, lock_type)
);

CREATE TABLE IF NOT EXISTS ax_lock_settings (
    chat_id BIGINT PRIMARY KEY,
    lockwarns BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS ax_allowlist (
    chat_id BIGINT NOT NULL,
    item TEXT NOT NULL,
    PRIMARY KEY (chat_id, item)
);

CREATE TABLE IF NOT EXISTS ax_blocklist (
    chat_id BIGINT NOT NULL,
    trigger TEXT NOT NULL,
    reason TEXT,
    PRIMARY KEY (chat_id, trigger)
);

CREATE TABLE IF NOT EXISTS ax_blocklist_settings (
    chat_id BIGINT PRIMARY KEY,
    mode TEXT NOT NULL DEFAULT 'nothing',
    mode_secs INT NOT NULL DEFAULT 3600,
    delete_msg BOOLEAN NOT NULL DEFAULT TRUE,
    default_reason TEXT
);

CREATE TABLE IF NOT EXISTS ax_raid_settings (
    chat_id BIGINT PRIMARY KEY,
    raid_until TIMESTAMPTZ,
    raid_secs INT NOT NULL DEFAULT 21600,
    action_secs INT NOT NULL DEFAULT 3600,
    auto_limit INT NOT NULL DEFAULT 0
);

ALTER TABLE ax_settings ADD COLUMN IF NOT EXISTS flood_mode TEXT NOT NULL DEFAULT 'tmute';
ALTER TABLE ax_settings ADD COLUMN IF NOT EXISTS flood_mode_secs INT NOT NULL DEFAULT 0;
ALTER TABLE ax_settings ADD COLUMN IF NOT EXISTS flood_clear BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS ax_rules (
    chat_id BIGINT PRIMARY KEY,
    rules_text TEXT,
    private BOOLEAN NOT NULL DEFAULT FALSE,
    button_text TEXT
);

CREATE TABLE IF NOT EXISTS ax_report_settings (
    chat_id BIGINT PRIMARY KEY,
    enabled BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS ax_pin_settings (
    chat_id BIGINT PRIMARY KEY,
    antichannelpin BOOLEAN NOT NULL DEFAULT FALSE,
    cleanlinked BOOLEAN NOT NULL DEFAULT FALSE
);

ALTER TABLE ax_settings ADD COLUMN IF NOT EXISTS clean_welcome BOOLEAN DEFAULT FALSE;
ALTER TABLE ax_settings ADD COLUMN IF NOT EXISTS last_welcome_id BIGINT;
ALTER TABLE ax_settings ADD COLUMN IF NOT EXISTS welcome_noformat BOOLEAN DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS ax_captcha_settings (
    chat_id BIGINT PRIMARY KEY,
    enabled BOOLEAN DEFAULT FALSE,
    mode TEXT DEFAULT 'button',
    mute_secs INT DEFAULT 0,
    kick BOOLEAN DEFAULT TRUE,
    kick_secs INT DEFAULT 300,
    rules BOOLEAN DEFAULT FALSE,
    btn_text TEXT
);

CREATE TABLE IF NOT EXISTS ax_captcha_pending (
    chat_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    answer TEXT,
    msg_id BIGINT,
    expires TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (chat_id, user_id)
);

CREATE TABLE IF NOT EXISTS ax_cleancmd (
    chat_id BIGINT NOT NULL,
    kind TEXT NOT NULL,
    PRIMARY KEY (chat_id, kind)
);

CREATE TABLE IF NOT EXISTS ax_cleanservice (
    chat_id BIGINT NOT NULL,
    kind TEXT NOT NULL,
    PRIMARY KEY (chat_id, kind)
);

CREATE TABLE IF NOT EXISTS ax_disabled (
    chat_id BIGINT NOT NULL,
    cmd TEXT NOT NULL,
    PRIMARY KEY (chat_id, cmd)
);

CREATE TABLE IF NOT EXISTS ax_disable_settings (
    chat_id BIGINT PRIMARY KEY,
    del_msg BOOLEAN DEFAULT FALSE,
    admin_too BOOLEAN DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS ax_log_settings (
    chat_id BIGINT PRIMARY KEY,
    log_chat BIGINT,
    off_cats TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS ax_misc_settings (
    chat_id BIGINT PRIMARY KEY,
    anon_admin BOOLEAN DEFAULT FALSE,
    admin_error BOOLEAN DEFAULT TRUE,
    silent_actions BOOLEAN DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS ax_note_settings (
    chat_id BIGINT PRIMARY KEY,
    private BOOLEAN NOT NULL DEFAULT FALSE
);
ALTER TABLE ax_connections ADD COLUMN IF NOT EXISTS last_chat_id BIGINT;

CREATE TABLE IF NOT EXISTS ax_user_lang (
    user_id BIGINT PRIMARY KEY,
    lang TEXT NOT NULL DEFAULT 'en'
);

CREATE TABLE IF NOT EXISTS ax_action_topics (
    chat_id BIGINT PRIMARY KEY,
    thread_id BIGINT NOT NULL
);
ALTER TABLE wallets ADD COLUMN IF NOT EXISTS last_topup_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS ax_watchlist (
    chat_id BIGINT PRIMARY KEY,
    note TEXT,
    added_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ax_watch_log (
    id BIGSERIAL PRIMARY KEY,
    chat_id BIGINT NOT NULL,
    message_id BIGINT,
    user_id BIGINT,
    name TEXT,
    body TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_watch_log_chat ON ax_watch_log(chat_id, created_at);

CREATE TABLE IF NOT EXISTS ax_bot_bans (
    user_id BIGINT PRIMARY KEY,
    banned_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ax_kill_status (
    user_id BIGINT PRIMARY KEY,
    dead_until TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS ax_referrals (
    new_user_id BIGINT PRIMARY KEY,
    referrer_id BIGINT NOT NULL,
    rewarded BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ax_shop_owned (
    user_id BIGINT NOT NULL,
    item_id TEXT NOT NULL,
    equipped BOOLEAN NOT NULL DEFAULT FALSE,
    purchased_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, item_id)
);
CREATE INDEX IF NOT EXISTS idx_ax_shop_equipped ON ax_shop_owned(user_id) WHERE equipped;

CREATE TABLE IF NOT EXISTS ax_channels (
    chat_id BIGINT PRIMARY KEY,
    title TEXT,
    added_by BIGINT,
    last_count INT,
    added_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ax_chan_cleanup (
    chat_id BIGINT NOT NULL,
    message_id BIGINT NOT NULL,
    delete_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (chat_id, message_id)
);
CREATE INDEX IF NOT EXISTS idx_ax_chan_cleanup_due ON ax_chan_cleanup(delete_at);

ALTER TABLE ax_channels ADD COLUMN IF NOT EXISTS last_promo TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE ax_channels ADD COLUMN IF NOT EXISTS week_count INT;
ALTER TABLE ax_channels ADD COLUMN IF NOT EXISTS week_at TIMESTAMPTZ NOT NULL DEFAULT now();
