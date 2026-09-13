import { WalletService } from "./WalletService";
import { TransactionService } from "./TransactionService";
import { ECONOMY_CONFIG } from "../config";
import { newId } from "../../core/ids";
import type { TransactionType } from "../../core/types";

export type PayResult =
  | { status: "ok"; newSenderBalance: number }
  | { status: "self_transfer" }
  | { status: "invalid_amount" }
  | { status: "below_minimum" }
  | { status: "above_maximum" }
  | { status: "insufficient_funds" }
  | { status: "daily_limit_exceeded" }
  | { status: "receiver_not_found" }
  | { status: "error"; message: string };

export class EconomyService {
  private wallets: WalletService;
  private tx: TransactionService;

  constructor(private db: D1Database) {
    this.wallets = new WalletService(db);
    this.tx = new TransactionService(db);
  }

  async getBalance(userId: string) {
    return this.wallets.getWallet(userId);
  }

  /**
   * Generic reward hook for future modules (Games, Achievements, Shop, etc).
   * They should call this instead of touching wallets/transactions directly.
   */
  async grantReward(
    userId: string,
    amounts: { cash?: number; gems?: number; xp?: number },
    source: string,
    type: TransactionType = "GAME_REWARD",
    idempotencyKey?: string
  ) {
    const wallet = await this.wallets.getWallet(userId);
    if (!wallet) throw new Error("Wallet not found");

    const cash = amounts.cash ?? 0;
    const gems = amounts.gems ?? 0;
    const xp = amounts.xp ?? 0;

    const statements = [
      this.db
        .prepare(`UPDATE wallets SET cash = cash + ?, gems = gems + ?, xp = xp + ?, updated_at = ? WHERE user_id = ?`)
        .bind(cash, gems, xp, Date.now(), userId),
    ];

    if (cash !== 0) {
      statements.push(
        this.tx.buildInsertStatement({
          userId,
          type,
          currency: "cash",
          amount: cash,
          balanceBefore: wallet.cash,
          balanceAfter: wallet.cash + cash,
          source,
          idempotencyKey,
        })
      );
    }
    if (gems !== 0) {
      statements.push(
        this.tx.buildInsertStatement({
          userId,
          type,
          currency: "gems",
          amount: gems,
          balanceBefore: wallet.gems,
          balanceAfter: wallet.gems + gems,
          source,
          idempotencyKey: idempotencyKey ? `${idempotencyKey}:gems` : undefined,
        })
      );
    }

    await this.db.batch(statements);
  }

  /**
   * Transfers cash between two users.
   *
   * D1 doesn't support interactive multi-statement transactions, so this is
   * done in two safe steps instead of pretending we have real 2PC:
   *
   *  1. Debit the sender with a single atomic conditional UPDATE
   *     (`WHERE cash >= amount`). If this affects 0 rows, nothing happened —
   *     no funds were created or destroyed. This step alone is race-proof.
   *  2. Credit the receiver + write both ledger rows in one db.batch()
   *     (atomic as a group). If this step fails for any reason, we refund
   *     the sender and log a REFUND transaction, so books always balance.
   */
  async pay(senderId: string, receiverUsername: string, amount: number): Promise<PayResult> {
    if (!Number.isInteger(amount) || amount <= 0) return { status: "invalid_amount" };
    if (amount < ECONOMY_CONFIG.MIN_TRANSFER) return { status: "below_minimum" };
    if (amount > ECONOMY_CONFIG.MAX_TRANSFER) return { status: "above_maximum" };

    const receiver = await this.wallets.getWalletByUsername(receiverUsername);
    if (!receiver) return { status: "receiver_not_found" };
    if (receiver.id === senderId) return { status: "self_transfer" };

    const sender = await this.wallets.getWallet(senderId);
    if (!sender) return { status: "error", message: "Sender wallet missing" };
    if (sender.cash < amount) return { status: "insufficient_funds" };

    const today = new Date().toISOString().slice(0, 10);
    const alreadySent = sender.daily_transferred_date === today ? sender.daily_transferred : 0;
    if (alreadySent + amount > ECONOMY_CONFIG.DAILY_TRANSFER_LIMIT) {
      return { status: "daily_limit_exceeded" };
    }

    // Step 1: atomic conditional debit. This is the only statement that
    // actually needs to be race-safe against concurrent /pay calls.
    const debitResult = await this.db
      .prepare(
        `UPDATE wallets SET cash = cash - ?, daily_transferred = ?, daily_transferred_date = ?, updated_at = ?
         WHERE user_id = ? AND cash >= ?`
      )
      .bind(amount, alreadySent + amount, today, Date.now(), senderId, amount)
      .run();

    if (debitResult.meta.changes === 0) {
      return { status: "insufficient_funds" }; // lost a race with a concurrent transfer
    }

    const requestId = newId();

    try {
      await this.db.batch([
        this.db
          .prepare(`UPDATE wallets SET cash = cash + ?, updated_at = ? WHERE user_id = ?`)
          .bind(amount, Date.now(), receiver.id),
        this.tx.buildInsertStatement({
          userId: senderId,
          type: "TRANSFER_SENT",
          currency: "cash",
          amount: -amount,
          balanceBefore: sender.cash,
          balanceAfter: sender.cash - amount,
          relatedUserId: receiver.id,
          source: "pay_command",
          idempotencyKey: `pay:${requestId}:sent`,
        }),
        this.tx.buildInsertStatement({
          userId: receiver.id,
          type: "TRANSFER_RECEIVED",
          currency: "cash",
          amount,
          balanceBefore: receiver.cash,
          balanceAfter: receiver.cash + amount,
          relatedUserId: senderId,
          source: "pay_command",
          idempotencyKey: `pay:${requestId}:received`,
        }),
      ]);
    } catch (err) {
      // Step 2 failed after the debit already committed — refund immediately
      // so the sender never loses money to an internal error.
      await this.db
        .prepare(`UPDATE wallets SET cash = cash + ?, updated_at = ? WHERE user_id = ?`)
        .bind(amount, Date.now(), senderId)
        .run();
      await this.tx
        .buildInsertStatement({
          userId: senderId,
          type: "REFUND",
          currency: "cash",
          amount,
          balanceBefore: sender.cash - amount,
          balanceAfter: sender.cash,
          source: "pay_command_failure_refund",
        })
        .run();
      console.error("Pay step 2 failed, refunded sender:", err);
      return { status: "error", message: "Transfer failed and was refunded" };
    }

    return { status: "ok", newSenderBalance: sender.cash - amount };
  }

  async getLeaderboard(category: "cash" | "xp" | "gems", page: number, pageSize: number) {
    // category is constrained to a TS union and only ever set by our own
    // command/callback code below — never interpolated from raw user input.
    const offset = (page - 1) * pageSize;
    const { results } = await this.db
      .prepare(
        `SELECT u.username as username, w.${category} as value
         FROM wallets w JOIN users u ON u.id = w.user_id
         ORDER BY w.${category} DESC
         LIMIT ? OFFSET ?`
      )
      .bind(pageSize, offset)
      .all();
    const totalRow = await this.db.prepare(`SELECT COUNT(*) as c FROM wallets`).first<{ c: number }>();
    return { results, total: totalRow?.c ?? 0 };
  }
}
