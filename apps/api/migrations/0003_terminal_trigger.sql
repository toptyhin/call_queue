-- Terminal status side effects + analysis chunk NOTIFY.

CREATE OR REPLACE FUNCTION call_attempts_terminal_side_effects()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    IF TG_OP = 'UPDATE'
       AND NEW.status IN ('completed', 'failed', 'no_answer')
       AND (OLD.status IS DISTINCT FROM NEW.status)
    THEN
        UPDATE contacts
        SET
            attempts_count = attempts_count + 1,
            last_attempt_at = NEW.ended_at,
            do_not_call = CASE
                WHEN COALESCE((NEW.outcome->>'do_not_call')::boolean, false)
                THEN true
                ELSE do_not_call
            END,
            locked_attempt_id = CASE
                WHEN locked_attempt_id = NEW.id THEN NULL
                ELSE locked_attempt_id
            END
        WHERE id = NEW.contact_id;

        INSERT INTO crm_outbox (attempt_id, status, outcome)
        VALUES (NEW.id, NEW.status, NEW.outcome);
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_call_attempts_terminal
    AFTER UPDATE OF status ON call_attempts
    FOR EACH ROW
    EXECUTE FUNCTION call_attempts_terminal_side_effects();

CREATE OR REPLACE FUNCTION analysis_chunks_notify()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    PERFORM pg_notify(
        'analysis_chunk',
        json_build_object(
            'analysis_id', NEW.analysis_id,
            'seq', NEW.seq
        )::text
    );
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_analysis_chunks_notify
    AFTER INSERT ON analysis_chunks
    FOR EACH ROW
    EXECUTE FUNCTION analysis_chunks_notify();

-- Status change notify for SSE terminal events
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
                'analysis_id', NEW.id,
                'status', NEW.status
            )::text
        );
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_analyses_status_notify
    AFTER UPDATE OF status ON analyses
    FOR EACH ROW
    EXECUTE FUNCTION analyses_status_notify();
