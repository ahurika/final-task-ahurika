exports.up = (pgm) => {
  pgm.sql(`
    CREATE TABLE tenants (
      id UUID PRIMARY KEY
    );

    CREATE TABLE tenant_api_keys (
      public_key_id TEXT PRIMARY KEY,
      tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
      secret_digest BYTEA NOT NULL
        CHECK (octet_length(secret_digest) = 32),
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      last_used_at TIMESTAMPTZ,
      revoked_at TIMESTAMPTZ,
      rotation_valid_until TIMESTAMPTZ
    );

    CREATE TABLE notifications (
      id UUID PRIMARY KEY,
      tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
      recipient_id TEXT NOT NULL
        CHECK (octet_length(recipient_id) <= 256),
      channel TEXT NOT NULL
        CHECK (channel IN ('email', 'push')),
      subject TEXT,
      body TEXT,
      title TEXT,
      status TEXT NOT NULL
        CHECK (status IN (
          'accepted',
          'queued',
          'processing',
          'retry_scheduled',
          'sent',
          'suppressed',
          'failed',
          'delivery_unknown'
        )),
      attempt_count INTEGER NOT NULL DEFAULT 0
        CHECK (attempt_count >= 0),
      suppression_reason TEXT
        CHECK (suppression_reason IN ('preference_missing', 'channel_disabled')),
      accepted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      terminal_at TIMESTAMPTZ,
      UNIQUE (tenant_id, id),
      CHECK (
        (status = 'suppressed' AND suppression_reason IS NOT NULL)
        OR (status <> 'suppressed' AND suppression_reason IS NULL)
      ),
      CHECK (
        (status IN ('sent', 'suppressed', 'failed', 'delivery_unknown')
          AND terminal_at IS NOT NULL)
        OR
        (status NOT IN ('sent', 'suppressed', 'failed', 'delivery_unknown')
          AND terminal_at IS NULL)
      ),
      CONSTRAINT notifications_rendered_content_check CHECK (
        (channel = 'email' AND title IS NULL
          AND (
            (subject IS NULL AND body IS NULL)
            OR (subject IS NOT NULL AND body IS NOT NULL)
          ))
        OR
        (channel = 'push' AND subject IS NULL
          AND (
            (title IS NULL AND body IS NULL)
            OR (title IS NOT NULL AND body IS NOT NULL)
          ))
      ),
      CONSTRAINT notifications_active_content_check CHECK (
        status IN ('sent', 'suppressed', 'failed', 'delivery_unknown')
        OR
        (channel = 'email' AND subject IS NOT NULL AND body IS NOT NULL)
        OR
        (channel = 'push' AND title IS NOT NULL AND body IS NOT NULL)
      )
    );

    CREATE TABLE notification_preferences (
      tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
      recipient_id TEXT NOT NULL
        CHECK (octet_length(recipient_id) <= 256),
      channel TEXT NOT NULL
        CHECK (channel IN ('email', 'push')),
      enabled BOOLEAN NOT NULL,
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      PRIMARY KEY (tenant_id, recipient_id, channel)
    );

    CREATE TABLE delivery_attempts (
      id UUID PRIMARY KEY,
      tenant_id UUID NOT NULL,
      notification_id UUID NOT NULL,
      attempt_number INTEGER NOT NULL CHECK (attempt_number > 0),
      state TEXT NOT NULL
        CHECK (state IN (
          'started',
          'provider_accepted',
          'retryable_failure',
          'permanent_failure',
          'timeout_ambiguous'
        )),
      started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      completed_at TIMESTAMPTZ,
      failure_code TEXT,
      CONSTRAINT delivery_attempt_completion_check CHECK (
        (state = 'started' AND completed_at IS NULL)
        OR
        (state <> 'started' AND completed_at IS NOT NULL)
      ),
      UNIQUE (tenant_id, notification_id, attempt_number),
      FOREIGN KEY (tenant_id, notification_id)
        REFERENCES notifications(tenant_id, id) ON DELETE RESTRICT
    );

    CREATE TABLE outbox_events (
      id UUID PRIMARY KEY,
      tenant_id UUID NOT NULL,
      notification_id UUID NOT NULL,
      state TEXT NOT NULL
        CHECK (state IN ('pending', 'claimed', 'completed')),
      available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      lease_expires_at TIMESTAMPTZ,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      completed_at TIMESTAMPTZ,
      UNIQUE (tenant_id, notification_id),
      FOREIGN KEY (tenant_id, notification_id)
        REFERENCES notifications(tenant_id, id) ON DELETE RESTRICT
    );

    CREATE TABLE idempotency_reservations (
      reservation_id UUID PRIMARY KEY,
      tenant_id UUID NOT NULL,
      idempotency_key TEXT COLLATE "C" NOT NULL,
      notification_id UUID NOT NULL,
      request_fingerprint BYTEA NOT NULL
        CHECK (octet_length(request_fingerprint) = 32),
      fingerprint_format_version SMALLINT NOT NULL DEFAULT 1
        CHECK (fingerprint_format_version > 0),
      hmac_key_version SMALLINT NOT NULL
        CHECK (hmac_key_version > 0),
      accepted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      expires_at TIMESTAMPTZ NOT NULL,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      UNIQUE (tenant_id, idempotency_key),
      UNIQUE (tenant_id, notification_id),
      FOREIGN KEY (tenant_id, notification_id)
        REFERENCES notifications(tenant_id, id) ON DELETE RESTRICT,
      CHECK (octet_length(idempotency_key) BETWEEN 1 AND 128),
      CHECK (idempotency_key ~ '^[!-~]{1,128}$'),
      CHECK (expires_at > accepted_at)
    );

    CREATE INDEX idempotency_reservations_expires_at_idx
      ON idempotency_reservations (expires_at);

    CREATE INDEX outbox_events_pending_available_idx
      ON outbox_events (available_at)
      WHERE state = 'pending';
  `);
};

exports.down = (pgm) => {
  pgm.sql(`
    DROP TABLE idempotency_reservations;
    DROP TABLE outbox_events;
    DROP TABLE delivery_attempts;
    DROP TABLE notification_preferences;
    DROP TABLE notifications;
    DROP TABLE tenant_api_keys;
    DROP TABLE tenants;
  `);
};
