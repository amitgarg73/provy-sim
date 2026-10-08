-- ITSM ingest keys: guarded clean-up (argus#1649, part b). Written 7 October 2026. PRE-PROD ONLY (project fpuyabfxtrzwciehfetk). NOT RUN.
--
-- WHAT THE READ-ONLY CHECK FOUND (pre-prod, 7 October 2026)
--   The issue said ITSM Demo has 5 active ingest keys and only 1 matches the console. It has 5 key ROWS and 1 ACTIVE one:
--     d4ab3a0d  2026-07-28 14:29  revoked 2026-07-28 14:42   "ITSM Incident Resolution ingest key"
--     9f5621f3  2026-07-28 14:42  revoked 2026-08-14 16:50   "ITSM Incident Resolution ingest key"
--     19b4f90e  2026-08-14 16:50  revoked 2026-09-09 21:43   "ITSM Incident Resolution ingest key"
--     19b1a287  2026-09-09 21:43  revoked 2026-09-10 04:44   "ServiceNow PDI dev193647 rebuild 2026-09-09"
--     b261991b  2026-09-10 04:39  ACTIVE                      "ITSM one key everywhere 2026-09-10 (argus#813)"
--   The one active key is the key in the simulator console (hash compared in SQL) and the key in the ServiceNow property provy.ingest.key
--   (scripts/fleet_doctor.py, read only, 7 October 2026). Three of the four places agree, and the fourth (GitHub secrets) holds the ServiceNow
--   login, not a Provy key. So the ITSM fleet needs no key revoked.
--   Across the whole pre-prod project, 64 of 73 keys are active and exactly ONE fleet has more than one active key: Northwind AP
--   (workflow 5be82335, two active keys: ab5b5924 "northwind-ap" and b0b19c4e "northwind retest 1053"). It is not a simulator fleet (no console row).
--
-- WHY NO KEY IS NAMED BELOW
--   The product records no use of an ingest key. ag_ingest_keys has no last_used column and the authenticator (web/lib/ingestAuth.ts) writes nothing
--   when a key is used, so "never used" and "not used in 30 days" cannot be proven for a single key. What can be read is the fleet's last step
--   (Northwind AP: 2026-09-20 04:18 UTC, 602 steps), which says nothing about WHICH of its two keys sent it. A key is therefore revoked here only if
--   a person names it AND the guards below hold. The list is empty. Filling it in is the founder's decision. Northwind AP is the only candidate and
--   it is a labelled ground-truth tenant (memory project_provy_northwind_oob), so it is deliberately not pre-filled.
--   (A product gap worth an issue: record last_used_at on ingest keys. Then this file could prove the claim by itself.)
--
-- CHECKED BEFORE HANDING OVER (read only: a READ ONLY transaction, rolled back, against pre-prod)
--   The file parses and runs with the empty list (revoked 0 keys, ITSM Demo 1 active of 5, Northwind AP 2 active of 2). With one id at a time it
--   refused: ITSM Demo's active key ("belongs to ITSM Demo"), Northwind's newer key ("younger than 30 days"), an already revoked key, an unknown id.
--
-- HOW TO USE
--   1. Put the key ids to revoke in `targets` below.
--   2. Run the whole file in one go. Every guard raises and rolls the transaction back; nothing is revoked unless all of them pass.
--   3. Check with the SELECT at the end (read only). To undo, run docs/itsm-ingest-keys-cleanup-2026-10-07-rollback.sql with the same ids.
--   Production (eckthcvacrkfjihluubt) is never touched by this file: the first guard refuses anything that is not pre-prod.

BEGIN;

DO $$
DECLARE
  targets      uuid[] := ARRAY[]::uuid[];     -- <- the founder's list. Empty: this file does nothing until it is filled in.
  itsm_wf      uuid   := '04a04e05-cc70-427a-a6fb-f1500d62ec90';   -- ITSM Demo's fleet; it is never allowed to lose its active key
  k            ag_ingest_keys%ROWTYPE;
  id_          uuid;
  revoked_n    int := 0;
BEGIN
  -- guard 0: pre-prod
  IF (SELECT name FROM app_environment LIMIT 1) IS DISTINCT FROM 'preprod' THEN
    RAISE EXCEPTION 'app_environment does not say preprod; refusing';
  END IF;

  -- guard 1: ITSM Demo still has exactly one active key and it is the one in the simulator console
  IF (SELECT count(*) FROM ag_ingest_keys WHERE workflow_id = itsm_wf AND revoked_at IS NULL) <> 1 THEN
    RAISE EXCEPTION 'ITSM Demo does not have exactly one active key; the picture this file was written against has changed';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM ag_ingest_keys ik JOIN sim_control_config sc ON sc.workflow_id::text = ik.workflow_id::text
    WHERE ik.workflow_id = itsm_wf AND ik.revoked_at IS NULL
      AND encode(sha256(convert_to(sc.ingest_key, 'UTF8')), 'hex') = ik.key_hash) THEN
    RAISE EXCEPTION 'ITSM Demo''s active key is not the simulator console''s key';
  END IF;

  FOREACH id_ IN ARRAY targets LOOP
    SELECT * INTO k FROM ag_ingest_keys WHERE id = id_;
    -- guard 2: the key exists and is active
    IF NOT FOUND THEN RAISE EXCEPTION 'key % does not exist', id_; END IF;
    IF k.revoked_at IS NOT NULL THEN RAISE EXCEPTION 'key % is already revoked', id_; END IF;
    -- guard 3: never ITSM Demo's fleet
    IF k.workflow_id = itsm_wf THEN RAISE EXCEPTION 'key % belongs to ITSM Demo; refusing', id_; END IF;
    -- guard 4: the fleet keeps at least one other active key
    IF (SELECT count(*) FROM ag_ingest_keys WHERE workflow_id = k.workflow_id AND revoked_at IS NULL AND id <> id_) < 1 THEN
      RAISE EXCEPTION 'key % is the only active key of its fleet; revoking it would cut the fleet off', id_;
    END IF;
    -- guard 5: it is not the key the simulator console holds for that fleet
    IF EXISTS (SELECT 1 FROM sim_control_config sc WHERE sc.workflow_id::text = k.workflow_id::text
                 AND encode(sha256(convert_to(sc.ingest_key, 'UTF8')), 'hex') = k.key_hash) THEN
      RAISE EXCEPTION 'key % is the simulator console''s key for its fleet', id_;
    END IF;
    -- guard 6: older than 30 days, and its fleet has sent nothing for 30 days (the strongest evidence of disuse the product keeps)
    IF k.created_at > now() - interval '30 days' THEN RAISE EXCEPTION 'key % is younger than 30 days', id_; END IF;
    IF EXISTS (SELECT 1 FROM ag_traces t WHERE t.workflow_id = k.workflow_id AND t.created_at > now() - interval '30 days') THEN
      RAISE EXCEPTION 'the fleet of key % sent steps in the last 30 days; use of this key cannot be ruled out', id_;
    END IF;
    UPDATE ag_ingest_keys SET revoked_at = now() WHERE id = id_ AND revoked_at IS NULL;
    revoked_n := revoked_n + 1;
  END LOOP;

  RAISE NOTICE 'revoked % key(s)', revoked_n;
END $$;

-- read only: the state after
SELECT w.name AS fleet, w.id AS workflow, count(*) FILTER (WHERE k.revoked_at IS NULL) AS active, count(*) AS total
FROM ag_ingest_keys k JOIN ag_workflows w ON w.id = k.workflow_id
GROUP BY w.name, w.id HAVING count(*) FILTER (WHERE k.revoked_at IS NULL) <> 1 OR w.id = '04a04e05-cc70-427a-a6fb-f1500d62ec90'
ORDER BY 1;

COMMIT;
