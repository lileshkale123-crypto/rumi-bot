import { newId } from "../../core/ids";
import type { TransactionType } from "../../core/types";

export interface LedgerEntryInput {
  userId: string;
  type: TransactionType;
  currency: "cash" | "gems" | "xp";
  amount: number; // positive for credit, negative for debit
  balanceBefore: number;
  balanceAfter: number;
  relatedUserId?: string | null;
  source: string;
  metadata?: Record<string, unknown>;
  /** Unique key that makes this exact reward/action impossible to duplicate. */
  idempotencyKey?: string | null;
}

export class TransactionService {
  constructor(private db: D1Database) {}

  /**
   * Builds (but does not run) an INSERT statement for one ledger row.
   * Callers combine this with wallet-update statements inside db.batch()
   * so the balance change and its audit record commit together atomically.
   */
  buildInsertStatement(entry: LedgerEntryInput): D1PreparedStatement {
    return this.db
      .prepare(
        `INSERT INTO transactions
         (id, user_id, type, currency, amount, balance_before, balance_after, related_user_id, source, metadata, idempotency_key, created_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
      )
      .bind(
        newId(),
        entry.userId,
        entry.type,
        entry.currency,
        entry.amount,
        entry.balanceBefore,
        entry.balanceAfter,
        entry.relatedUserId ?? null,
        entry.source,
        entry.metadata ? JSON.stringify(entry.metadata) : null,
        entry.idempotencyKey ?? null,
        Date.now()
      );
  }

  async getByIdempotencyKey(key: string) {
    return this.db.prepare(`SELECT * FROM transactions WHERE idempotency_key = ?`).bind(key).first();
  }

  async getHistory(userId: string, limit = 20) {
    const { results } = await this.db
      .prepare(`SELECT * FROM transactions WHERE user_id = ? ORDER BY created_at DESC LIMIT ?`)
      .bind(userId, limit)
      .all();
    return results;
  }
}
