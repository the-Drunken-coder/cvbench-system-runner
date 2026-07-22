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

    await this.db
      .prepare(`INSERT INTO submissions (
        id, status, image, argv_json, name, model_version, contact, notes,
        idempotency_key, request_sha256, submitter_key_sha256, created_at, updated_at
      ) SELECT ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
      WHERE (
        SELECT COUNT(*) FROM submissions
        WHERE submitter_key_sha256 = ? AND created_at >= ?
      ) < ?
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
        row.submitterKeyHash,
        row.now - 3600,
        maxPerHour,
      )
      .run();

    const stored = await this.db
      .prepare("SELECT id, request_sha256 FROM submissions WHERE submitter_key_sha256 = ? AND idempotency_key = ?")
      .bind(row.submitterKeyHash, row.idempotencyKey)
      .first();
    if (!stored) return { kind: "rate_limited" };
    if (stored.request_sha256 !== row.requestHash) return { kind: "conflict" };
    return { kind: stored.id === row.id ? "created" : "replay", submission: await this.getSubmission(stored.id) };
  }

  async getSubmission(id) {
    const row = await this.db.prepare("SELECT * FROM submissions WHERE id = ?").bind(id).first();
    return row ? deserialize(row) : null;
  }

  async listSubmissions({ status, model, limit, cursor = null }) {
    const clauses = [];
    const bindings = [];
    if (status) {
      clauses.push("status = ?");
      bindings.push(status);
    }
    if (model) {
      clauses.push("(name LIKE ? OR image LIKE ?)");
      bindings.push(`%${model}%`, `%${model}%`);
    }
    if (cursor) {
      clauses.push("(created_at < ? OR (created_at = ? AND id < ?))");
      bindings.push(cursor.createdAt, cursor.createdAt, cursor.id);
    }
    const where = clauses.length ? `WHERE ${clauses.join(" AND ")}` : "";
    const result = await this.db
      .prepare(`SELECT * FROM submissions ${where} ORDER BY created_at DESC, id DESC LIMIT ?`)
      .bind(...bindings, limit + 1)
      .all();
    const rows = (result.results || []).map(deserialize);
    const hasMore = rows.length > limit;
    const page = rows.slice(0, limit);
    const last = page.at(-1);
    return { rows: page, nextCursor: hasMore && last ? { createdAt: last.createdAt, id: last.id } : null };
  }

  async addOperatorNote({ id, submissionId, verdict, note, createdAt, operatorKeyHash }) {
    await this.db
      .prepare(`INSERT INTO operator_notes (id, submission_id, verdict, note, created_at, operator_key_sha256)
        VALUES (?, ?, ?, ?, ?, ?)`)
      .bind(id, submissionId, verdict, note, createdAt, operatorKeyHash)
      .run();
    return { id, submissionId, verdict, note, createdAt };
  }

  async listOperatorNotes(submissionId) {
    const result = await this.db
      .prepare("SELECT id, submission_id, verdict, note, created_at FROM operator_notes WHERE submission_id = ? ORDER BY created_at ASC, id ASC")
      .bind(submissionId)
      .all();
    return (result.results || []).map((row) => ({
      id: row.id,
      submissionId: row.submission_id,
      verdict: row.verdict,
      note: row.note,
      createdAt: row.created_at,
    }));
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
