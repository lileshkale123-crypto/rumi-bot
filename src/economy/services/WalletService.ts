import type { Wallet } from "../../core/types";

export class WalletService {
  constructor(private db: D1Database) {}

  /**
   * Creates the user + wallet + streak rows if they don't exist yet.
   * Safe to call on every command — it's a cheap no-op for existing users.
   */
  async ensureUserAndWallet(userId: string, username: string | undefined): Promise<void> {
    const now = Date.now();
    await this.db.batch([
      this.db
        .prepare(
          `INSERT INTO users (id, username, created_at, updated_at) VALUES (?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET username = excluded.username, updated_at = excluded.updated_at`
        )
        .bind(userId, username ?? null, now, now),
      this.db
        .prepare(
          `INSERT INTO wallets (user_id, cash, gems, xp, level, created_at, updated_at)
           VALUES (?, 0, 0, 0, 1, ?, ?)
           ON CONFLICT(user_id) DO NOTHING`
        )
        .bind(userId, now, now),
      this.db
        .prepare(
          `INSERT INTO streaks (user_id, current_streak, longest_streak, last_claim_at, milestones_claimed)
           VALUES (?, 0, 0, NULL, '[]')
           ON CONFLICT(user_id) DO NOTHING`
        )
        .bind(userId),
    ]);
  }

  async getWallet(userId: string): Promise<Wallet | null> {
    const row = await this.db.prepare(`SELECT * FROM wallets WHERE user_id = ?`).bind(userId).first<Wallet>();
    return row ?? null;
  }

  /** Looks up a wallet by @username (used for /pay). Returns the user id too. */
  async getWalletByUsername(username: string): Promise<(Wallet & { id: string }) | null> {
    const row = await this.db
      .prepare(
        `SELECT u.id as id, w.* FROM users u
         JOIN wallets w ON w.user_id = u.id
         WHERE u.username = ? COLLATE NOCASE`
      )
      .bind(username)
      .first<Wallet & { id: string }>();
    return row ?? null;
  }
}
