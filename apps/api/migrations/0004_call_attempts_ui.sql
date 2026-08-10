-- List/detail UI: index, CRM read policy for app_user, NOTIFY for SSE feed.

CREATE INDEX IF NOT EXISTS idx_call_attempts_org_created
    ON call_attempts (org_id, created_at DESC, id DESC);

-- Allow tenant read of CRM outbox via linked attempt (poller still BYPASSRLS).
DROP POLICY IF EXISTS crm_outbox_deny ON crm_outbox;

CREATE POLICY crm_outbox_select_via_attempt ON crm_outbox
    FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM call_attempts a
            WHERE a.id = crm_outbox.attempt_id
              AND a.org_id = current_setting('app.org_id')::uuid
        )
    );

-- Wake SSE clients when call attempt status changes (insert or status update).
CREATE OR REPLACE FUNCTION call_attempts_status_notify()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    IF TG_OP = 'INSERT'
       OR (TG_OP = 'UPDATE' AND NEW.status IS DISTINCT FROM OLD.status)
    THEN
        PERFORM pg_notify(
            'call_attempt_status',
            json_build_object(
                'org_id', NEW.org_id,
                'attempt_id', NEW.id,
                'status', NEW.status
            )::text
        );
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_call_attempts_status_notify ON call_attempts;
CREATE TRIGGER trg_call_attempts_status_notify
    AFTER INSERT OR UPDATE OF status ON call_attempts
    FOR EACH ROW
    EXECUTE FUNCTION call_attempts_status_notify();

-- Wake SSE clients when CRM outbox delivery state changes.
CREATE OR REPLACE FUNCTION crm_outbox_notify()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_org_id uuid;
BEGIN
    SELECT org_id INTO v_org_id FROM call_attempts WHERE id = NEW.attempt_id;
    IF v_org_id IS NULL THEN
        RETURN NEW;
    END IF;
    PERFORM pg_notify(
        'crm_delivery',
        json_build_object(
            'org_id', v_org_id,
            'attempt_id', NEW.attempt_id,
            'outbox_id', NEW.id
        )::text
    );
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_crm_outbox_notify ON crm_outbox;
CREATE TRIGGER trg_crm_outbox_notify
    AFTER INSERT OR UPDATE ON crm_outbox
    FOR EACH ROW
    EXECUTE FUNCTION crm_outbox_notify();

-- Enrich analysis status notify with org_id + call_attempt_id for org SSE feed.
CREATE OR REPLACE FUNCTION analyses_status_notify()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    IF NEW.status IS DISTINCT FROM OLD.status THEN
        PERFORM pg_notify(
            'analysis_status',
            json_build_object(
                'org_id', NEW.org_id,
                'analysis_id', NEW.id,
                'call_attempt_id', NEW.call_attempt_id,
                'status', NEW.status
            )::text
        );
    END IF;
    RETURN NEW;
END;
$$;
