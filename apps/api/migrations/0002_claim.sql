-- Roles, RLS, claim indexes, claim_next_contact.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_user') THEN
        CREATE ROLE app_user LOGIN PASSWORD '__APP_USER_PASSWORD__';
    ELSE
        ALTER ROLE app_user WITH LOGIN PASSWORD '__APP_USER_PASSWORD__';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_webhook') THEN
        CREATE ROLE app_webhook LOGIN PASSWORD '__APP_WEBHOOK_PASSWORD__' BYPASSRLS;
    ELSE
        ALTER ROLE app_webhook WITH LOGIN PASSWORD '__APP_WEBHOOK_PASSWORD__' BYPASSRLS;
    END IF;
END
$$;

GRANT CONNECT ON DATABASE postgres TO app_user, app_webhook;
-- Note: database name is fixed by launch contract (postgres).
GRANT USAGE ON SCHEMA public TO app_user, app_webhook;

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO app_user, app_webhook;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_user, app_webhook;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_user, app_webhook;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO app_user, app_webhook;

-- schema_migrations is admin-only
REVOKE ALL ON TABLE schema_migrations FROM app_user, app_webhook;

-- Claim candidate index
CREATE INDEX idx_contacts_claim_candidates
    ON contacts(campaign_id)
    WHERE do_not_call = false AND locked_attempt_id IS NULL;

CREATE UNIQUE INDEX uq_call_attempts_provider_call_id
    ON call_attempts(provider_call_id)
    WHERE provider_call_id IS NOT NULL;

CREATE UNIQUE INDEX uq_webhook_events_provider_event_id
    ON webhook_events(provider_event_id);

-- RLS on tenant tables
ALTER TABLE orgs ENABLE ROW LEVEL SECURITY;
ALTER TABLE campaigns ENABLE ROW LEVEL SECURITY;
ALTER TABLE contacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE call_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE analyses ENABLE ROW LEVEL SECURITY;
ALTER TABLE analysis_chunks ENABLE ROW LEVEL SECURITY;

-- webhook_events / crm_outbox: accessed via service role or joins; enable RLS with org via attempt
ALTER TABLE webhook_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE crm_outbox ENABLE ROW LEVEL SECURITY;

CREATE POLICY orgs_isolation ON orgs
    USING (id = current_setting('app.org_id')::uuid)
    WITH CHECK (id = current_setting('app.org_id')::uuid);

CREATE POLICY campaigns_isolation ON campaigns
    USING (org_id = current_setting('app.org_id')::uuid)
    WITH CHECK (org_id = current_setting('app.org_id')::uuid);

CREATE POLICY contacts_isolation ON contacts
    USING (org_id = current_setting('app.org_id')::uuid)
    WITH CHECK (org_id = current_setting('app.org_id')::uuid);

CREATE POLICY call_attempts_isolation ON call_attempts
    USING (org_id = current_setting('app.org_id')::uuid)
    WITH CHECK (org_id = current_setting('app.org_id')::uuid);

CREATE POLICY analyses_isolation ON analyses
    USING (org_id = current_setting('app.org_id')::uuid)
    WITH CHECK (org_id = current_setting('app.org_id')::uuid);

CREATE POLICY analysis_chunks_isolation ON analysis_chunks
    USING (
        EXISTS (
            SELECT 1 FROM analyses a
            WHERE a.id = analysis_chunks.analysis_id
              AND a.org_id = current_setting('app.org_id')::uuid
        )
    )
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM analyses a
            WHERE a.id = analysis_chunks.analysis_id
              AND a.org_id = current_setting('app.org_id')::uuid
        )
    );

-- webhook_events: visible to app_user only via linked attempt of the same org
-- (needed for provider-link buffer sweep). app_webhook bypasses RLS.
CREATE POLICY webhook_events_via_attempt ON webhook_events
    USING (
        EXISTS (
            SELECT 1 FROM call_attempts a
            WHERE a.provider_call_id = webhook_events.provider_call_id
              AND a.org_id = current_setting('app.org_id')::uuid
        )
    )
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM call_attempts a
            WHERE a.provider_call_id = webhook_events.provider_call_id
              AND a.org_id = current_setting('app.org_id')::uuid
        )
    );

-- crm_outbox: only SECURITY DEFINER trigger inserts; poller uses BYPASSRLS role.
CREATE POLICY crm_outbox_deny ON crm_outbox
    USING (false)
    WITH CHECK (false);

-- FORCE RLS so table owner (postgres) also respects policies when SET ROLE isn't used;
-- app connects as app_user / app_webhook directly.
ALTER TABLE orgs FORCE ROW LEVEL SECURITY;
ALTER TABLE campaigns FORCE ROW LEVEL SECURITY;
ALTER TABLE contacts FORCE ROW LEVEL SECURITY;
ALTER TABLE call_attempts FORCE ROW LEVEL SECURITY;
ALTER TABLE analyses FORCE ROW LEVEL SECURITY;
ALTER TABLE analysis_chunks FORCE ROW LEVEL SECURITY;
ALTER TABLE webhook_events FORCE ROW LEVEL SECURITY;
ALTER TABLE crm_outbox FORCE ROW LEVEL SECURITY;

CREATE OR REPLACE FUNCTION claim_next_contact(p_campaign_id uuid)
RETURNS TABLE(contact_id uuid, phone_e164 text, attempt_id uuid)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_org_id uuid;
    v_campaign_status text;
    v_contact_id uuid;
    v_phone text;
    v_attempt_id uuid;
BEGIN
    BEGIN
        v_org_id := current_setting('app.org_id')::uuid;
    EXCEPTION
        WHEN others THEN
            RAISE EXCEPTION 'app.org_id is not set' USING ERRCODE = 'P0001';
    END;

    SELECT c.status INTO v_campaign_status
    FROM campaigns c
    WHERE c.id = p_campaign_id AND c.org_id = v_org_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'campaign not found' USING ERRCODE = 'P0002';
    END IF;

    -- Inactive campaign → no eligible contacts (not an error).
    IF v_campaign_status IS DISTINCT FROM 'active' THEN
        RETURN;
    END IF;

    SELECT ct.id, ct.phone_e164
    INTO v_contact_id, v_phone
    FROM contacts ct
    WHERE ct.campaign_id = p_campaign_id
      AND ct.org_id = v_org_id
      AND ct.do_not_call = false
      AND ct.locked_attempt_id IS NULL
      AND ct.attempts_count < 3
      AND (ct.last_attempt_at IS NULL OR ct.last_attempt_at <= now() - interval '4 hours')
      AND (now() AT TIME ZONE ct.timezone)::time >= time '09:00'
      AND (now() AT TIME ZONE ct.timezone)::time < time '20:00'
    ORDER BY ct.id
    FOR UPDATE OF ct SKIP LOCKED
    LIMIT 1;

    IF v_contact_id IS NULL THEN
        RETURN;
    END IF;

    INSERT INTO call_attempts (org_id, campaign_id, contact_id, status)
    VALUES (v_org_id, p_campaign_id, v_contact_id, 'queued')
    RETURNING id INTO v_attempt_id;

    UPDATE contacts
    SET locked_attempt_id = v_attempt_id
    WHERE id = v_contact_id;

    contact_id := v_contact_id;
    phone_e164 := v_phone;
    attempt_id := v_attempt_id;
    RETURN NEXT;
END;
$$;

REVOKE ALL ON FUNCTION claim_next_contact(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION claim_next_contact(uuid) TO app_user;
