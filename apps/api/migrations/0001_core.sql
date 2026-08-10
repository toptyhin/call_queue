-- Fixed schema from the assignment + extensions from ASSUMPTIONS §2.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE orgs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL
);

CREATE TABLE campaigns (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id uuid NOT NULL REFERENCES orgs(id),
    name text NOT NULL,
    status text NOT NULL CHECK (status IN ('draft', 'active', 'paused'))
);

CREATE TABLE contacts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id uuid NOT NULL REFERENCES orgs(id),
    campaign_id uuid NOT NULL REFERENCES campaigns(id),
    phone_e164 text NOT NULL,
    timezone text NOT NULL,
    attempts_count int NOT NULL DEFAULT 0,
    last_attempt_at timestamptz,
    do_not_call boolean NOT NULL DEFAULT false,
    locked_attempt_id uuid
);

CREATE TABLE call_attempts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id uuid NOT NULL REFERENCES orgs(id),
    campaign_id uuid NOT NULL REFERENCES campaigns(id),
    contact_id uuid NOT NULL REFERENCES contacts(id),
    provider_call_id text,
    status text NOT NULL,
    started_at timestamptz,
    ended_at timestamptz,
    outcome jsonb,
    last_applied_sequence bigint NOT NULL DEFAULT 0,
    transcript text,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- Deferred FK: contacts.locked_attempt_id → call_attempts.id
ALTER TABLE contacts
    ADD CONSTRAINT contacts_locked_attempt_id_fkey
    FOREIGN KEY (locked_attempt_id) REFERENCES call_attempts(id);

CREATE TABLE webhook_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    provider_event_id text NOT NULL,
    provider_call_id text NOT NULL,
    sequence bigint NOT NULL,
    type text NOT NULL,
    payload jsonb NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    applied_at timestamptz,
    call_attempt_id uuid REFERENCES call_attempts(id)
);

CREATE TABLE crm_outbox (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    attempt_id uuid NOT NULL REFERENCES call_attempts(id),
    status text NOT NULL,
    outcome jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    delivered_at timestamptz,
    attempts int NOT NULL DEFAULT 0,
    last_error text,
    next_attempt_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE analyses (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id uuid NOT NULL REFERENCES orgs(id),
    call_attempt_id uuid NOT NULL REFERENCES call_attempts(id),
    status text NOT NULL CHECK (status IN ('queued', 'streaming', 'done', 'error', 'cancelled')),
    result jsonb,
    partial jsonb,
    error text,
    cancel_requested boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE analysis_chunks (
    id bigserial PRIMARY KEY,
    analysis_id uuid NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
    seq bigint NOT NULL,
    field text NOT NULL,
    delta text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (analysis_id, seq)
);

CREATE INDEX idx_campaigns_org ON campaigns(org_id);
CREATE INDEX idx_contacts_org ON contacts(org_id);
CREATE INDEX idx_call_attempts_org ON call_attempts(org_id);
CREATE INDEX idx_call_attempts_contact ON call_attempts(contact_id);
CREATE INDEX idx_webhook_events_call_id ON webhook_events(provider_call_id);
CREATE INDEX idx_crm_outbox_pending ON crm_outbox(next_attempt_at)
    WHERE delivered_at IS NULL;
CREATE INDEX idx_analyses_org ON analyses(org_id);
CREATE INDEX idx_analyses_status ON analyses(status)
    WHERE status IN ('queued', 'streaming');
CREATE INDEX idx_analysis_chunks_analysis_seq ON analysis_chunks(analysis_id, seq);
CREATE INDEX idx_call_attempts_active_created ON call_attempts(created_at)
    WHERE status IN ('queued', 'dialing', 'in_progress');
