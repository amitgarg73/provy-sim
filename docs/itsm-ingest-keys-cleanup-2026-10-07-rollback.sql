-- Rollback for docs/itsm-ingest-keys-cleanup-2026-10-07.sql (argus#1649, part b). PRE-PROD ONLY. NOT RUN.
-- A revoked key is a key whose revoked_at is set, nothing more (the hash stays), so undoing is clearing it again. Put the SAME ids as the
-- clean-up file in `targets`. Only a key revoked in the last day is restored, so an older, deliberate revocation is never undone by mistake.
-- Note the product keeps a short memo of valid keys (30 seconds): a restored key works again within that time.

BEGIN;

DO $$
DECLARE
  targets uuid[] := ARRAY[]::uuid[];     -- <- the ids the clean-up revoked
  id_ uuid;
  n int := 0;
BEGIN
  IF (SELECT name FROM app_environment LIMIT 1) IS DISTINCT FROM 'preprod' THEN
    RAISE EXCEPTION 'app_environment does not say preprod; refusing';
  END IF;
  FOREACH id_ IN ARRAY targets LOOP
    IF NOT EXISTS (SELECT 1 FROM ag_ingest_keys WHERE id = id_ AND revoked_at IS NOT NULL AND revoked_at > now() - interval '1 day') THEN
      RAISE EXCEPTION 'key % is not a key revoked in the last day; refusing to touch it', id_;
    END IF;
    UPDATE ag_ingest_keys SET revoked_at = NULL, revoked_by = NULL WHERE id = id_;
    n := n + 1;
  END LOOP;
  RAISE NOTICE 'restored % key(s)', n;
END $$;

COMMIT;
