export class D1Store {
  constructor(db) {
    this.db = db;
  }

  async health() {
    await this.db.prepare("SELECT COUNT(*) AS count FROM submissions").first();
  }

  async createSubmission(row, maxPerHour) {
    const existing = await this.db
      .prepare("SELECT id, request_sha256 FROM submissions WHERE submitter_key_sha256 = ? AND idempotency_key = ?")
      .bind(row.submitterKeyHash, row.idempotencyKey)
      .first();
    if (existing) {
      return existing.request_sha256 === row.requestHash
        ? { kind: "replay", submission: await this.getSubmission(existing.id) }
        : { kind: "conflict" };
    }

    const recent = await this.db
      .prepare("SELECT COUNT(*) AS count FROM submissions WHERE submitter_key_sha256 = ? AND created_at >= ?")
      .bind(row.submitterKeyHash, row.now - 3600)
      .first();
    if (Number(recent?.count || 0) >= maxPerHour) return { kind: "rate_limited" };

    await this.db
      .prepare(`INSERT INTO submissions (
        id, status, image, argv_json, name, model_version, contact, notes,
        idempotency_key, request_sha256, submitter_key_sha256, created_at, updated_at
      ) VALUES (?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      ON CONFLICT (submitter_key_sha256, idempotency_key) DO NOTHING`)
      .bind(
        row.id,
        row.image,
        JSON.stringify(row.argv),
        row.name,
        row.modelVersion,
        row.contact,
        row.notes,
        row.idempotencyKey,
        row.requestHash,
        row.submitterKeyHash,
        row.now,
        row.now,
      )
      .run();

    const stored = await this.db
      .prepare("SELECT id, request_sha256 FROM submissions WHERE submitter_key_sha256 = ? AND idempotency_key = ?")
      .bind(row.submitterKeyHash, row.idempotencyKey)
      .first();
    if (stored.request_sha256 !== row.requestHash) return { kind: "conflict" };
    return { kind: stored.id === row.id ? "created" : "replay", submission: await this.getSubmission(stored.id) };
  }

  async getSubmission(id) {
    const row = await this.db.prepare("SELECT * FROM submissions WHERE id = ?").bind(id).first();
    return row ? deserialize(row) : null;
  }

  async leaseJob({ now, leaseExpiresAt, leaseTokenHash }) {
    await this.requeueExpired(now);

    for (let attempt = 0; attempt < 3; attempt += 1) {
      const queued = await this.db
        .prepare("SELECT id FROM submissions WHERE status = 'queued' ORDER BY created_at, id LIMIT 1")
        .first();
      if (!queued) return null;
      const changed = await this.db
        .prepare(`UPDATE submissions SET status = 'running', lease_token_sha256 = ?,
          lease_expires_at = ?, attempt = attempt + 1, started_at = COALESCE(started_at, ?), updated_at = ?
          WHERE id = ? AND status = 'queued'`)
        .bind(leaseTokenHash, leaseExpiresAt, now, now, queued.id)
        .run();
      if (Number(changed.meta?.changes || 0) === 1) return this.getSubmission(queued.id);
    }
    return null;
  }

  async requeueExpired(now) {
    const changed = await this.db
      .prepare(`UPDATE submissions SET status = 'queued', lease_token_sha256 = NULL,
        lease_expires_at = NULL, updated_at = ?
        WHERE status = 'running' AND lease_expires_at < ?`)
      .bind(now, now)
      .run();
    return Number(changed.meta?.changes || 0);
  }

  async completeJob({ id, leaseTokenHash, status, report, error, now }) {
    const changed = await this.db
      .prepare(`UPDATE submissions SET status = ?, result_json = ?, error = ?, completed_at = ?,
        updated_at = ?, lease_token_sha256 = NULL, lease_expires_at = NULL
        WHERE id = ? AND status = 'running' AND lease_token_sha256 = ? AND lease_expires_at >= ?`)
      .bind(status, report === null ? null : JSON.stringify(report), error, now, now, id, leaseTokenHash, now)
      .run();
    return Number(changed.meta?.changes || 0) === 1 ? this.getSubmission(id) : null;
  }
}

function deserialize(row) {
  return {
    id: row.id,
    status: row.status,
    image: row.image,
    argv: JSON.parse(row.argv_json),
    name: row.name,
    modelVersion: row.model_version,
    contact: row.contact,
    notes: row.notes,
    attempt: row.attempt,
    result: row.result_json ? JSON.parse(row.result_json) : null,
    error: row.error,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
    startedAt: row.started_at,
    completedAt: row.completed_at,
    leaseExpiresAt: row.lease_expires_at,
  };
}
