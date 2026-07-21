export class MemoryStore {
  constructor() {
    this.rows = new Map();
  }

  async health() {}

  async createSubmission(row, maxPerHour) {
    const existing = [...this.rows.values()].find(
      (item) => item.submitterKeyHash === row.submitterKeyHash && item.idempotencyKey === row.idempotencyKey,
    );
    if (existing) {
      return existing.requestHash === row.requestHash
        ? { kind: "replay", submission: clone(existing) }
        : { kind: "conflict" };
    }
    const recent = [...this.rows.values()].filter(
      (item) => item.submitterKeyHash === row.submitterKeyHash && item.createdAt >= row.now - 3600,
    );
    if (recent.length >= maxPerHour) return { kind: "rate_limited" };
    const stored = {
      ...row,
      status: "queued",
      attempt: 0,
      result: null,
      error: null,
      createdAt: row.now,
      updatedAt: row.now,
      startedAt: null,
      completedAt: null,
      leaseExpiresAt: null,
      leaseTokenHash: null,
    };
    this.rows.set(row.id, stored);
    return { kind: "created", submission: clone(stored) };
  }

  async getSubmission(id) {
    return this.rows.has(id) ? clone(this.rows.get(id)) : null;
  }

  async leaseJob({ now, leaseExpiresAt, leaseTokenHash }) {
    await this.requeueExpired(now);
    const queued = [...this.rows.values()]
      .filter((row) => row.status === "queued")
      .sort((left, right) => left.createdAt - right.createdAt || left.id.localeCompare(right.id))[0];
    if (!queued) return null;
    Object.assign(queued, {
      status: "running",
      attempt: queued.attempt + 1,
      startedAt: queued.startedAt || now,
      updatedAt: now,
      leaseExpiresAt,
      leaseTokenHash,
    });
    return clone(queued);
  }

  async requeueExpired(now) {
    let count = 0;
    for (const row of this.rows.values()) {
      if (row.status === "running" && row.leaseExpiresAt < now) {
        Object.assign(row, { status: "queued", leaseExpiresAt: null, leaseTokenHash: null, updatedAt: now });
        count += 1;
      }
    }
    return count;
  }

  async completeJob({ id, leaseTokenHash, status, report, error, now }) {
    const row = this.rows.get(id);
    if (!row || row.status !== "running" || row.leaseTokenHash !== leaseTokenHash || row.leaseExpiresAt < now) {
      return null;
    }
    Object.assign(row, {
      status,
      result: report,
      error,
      completedAt: now,
      updatedAt: now,
      leaseTokenHash: null,
      leaseExpiresAt: null,
    });
    return clone(row);
  }
}

function clone(value) {
  return structuredClone(value);
}
