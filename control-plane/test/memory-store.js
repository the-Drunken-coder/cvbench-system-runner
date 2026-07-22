export class MemoryStore {
  constructor() {
    this.rows = new Map();
    this.notes = new Map();
    this.createTail = Promise.resolve();
  }

  async health() {}

  async createSubmission(row, maxPerHour) {
    const operation = this.createTail.then(() => this.createSubmissionAtomic(row, maxPerHour));
    this.createTail = operation.catch(() => {});
    return operation;
  }

  createSubmissionAtomic(row, maxPerHour) {
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

  async listSubmissions({ status, model, limit, cursor = null }) {
    const rows = [...this.rows.values()]
      .filter((row) => (!status || row.status === status) && (!model || row.name.includes(model) || row.image.includes(model)))
      .filter((row) => !cursor || row.createdAt < cursor.createdAt || (row.createdAt === cursor.createdAt && row.id < cursor.id))
      .sort((left, right) => right.createdAt - left.createdAt || right.id.localeCompare(left.id))
      .map(clone);
    const page = rows.slice(0, limit);
    const last = page.at(-1);
    return { rows: page, nextCursor: rows.length > limit && last ? { createdAt: last.createdAt, id: last.id } : null };
  }

  async addOperatorNote({ id, submissionId, verdict, note, createdAt }) {
    const stored = { id, submissionId, verdict, note, createdAt };
    this.notes.set(id, stored);
    return clone(stored);
  }

  async listOperatorNotes(submissionId) {
    return [...this.notes.values()]
      .filter((note) => note.submissionId === submissionId)
      .sort((left, right) => left.createdAt - right.createdAt || left.id.localeCompare(right.id))
      .map(clone);
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
