# Worker OOM incident and release health gate — 2026-10-02

Read-only production evidence: image `206a1a144be4e0c5de52238c273f89e908d4a84d`, container `6be4cfa70e11c7ba3f6a12932a26b1cba5f3a10e58691005f833e0e794a0be7d`, limit 256 MiB, restart policy unless-stopped. Kernel records 57 CONSTRAINT_MEMCG OOM kills for this exact container from 07:02 to 07:16:33 UTC (10:02–10:16:33 Moscow). Last killed process used anon-rss 258024 kB and file-rss 25856 kB. Historical Docker exit events aged out; historical exit code is UNAVAILABLE. Current running invocation ExitCode=0/OOMKilled=false does not describe historical kills. RestartCount reached 173; a subsequent 35-second observation had delta=0. Current approximately 100 MiB usage does not identify the historic allocation source or rule out a leak.

Minimal containment raises ONLY Worker memory limit to 512 MiB. Host has 2 GiB RAM; read-only measurement showed about 1 GiB available. This is a proposed production change, not an applied change. Production ADS_AUTOPILOT_ENABLED and ADS_WRITE_ACTIONS_ENABLED remain false. No database migration.

The runtime writes an atomic heartbeat after a successful PRIMARY polling response and durable marker handling. The timestamp advances even when there are no updates and the cursor is unchanged. The shared read-only preflight collector checks exact running release and namespace PID, ADS flags, heartbeat freshness, stable container ID/StartedAt, and zero restart-count delta over 35 seconds. A recently started worker gets bounded startup grace before the baseline. No restart within the observation interval is excused. After an intentional release restart, postdeploy starts a fresh baseline using the same collector.

Legacy production lacks this primary heartbeat. Generic /updates log entries may belong to custom bots; therefore the strict gate reports primary_loop_unverified, rather than a false PASS. Initial adoption remains BLOCKED for production apply until an explicit, reviewed bootstrap/remediation procedure is approved. No health exception or legacy waiver is enabled in this candidate.

The original containment candidate also used a network-isolated synthetic 320 MiB allocation in the candidate image to compare 256 MiB and 512 MiB limits. That historical check did not replay the production Worker workload and is no longer a blocking Prepare gate. Release-time memory protection instead reads the deployed Worker's configured Docker limit and kernel cgroup limit (both must be exactly 512 MiB), OOM state, stable identity and restart count, and advancing primary heartbeat across the existing 35-second observation. The historical incident evidence and reason for raising the Worker limit remain documented above. Local runtime tests use fake clients/DB and no production tokens.

## Reviewed initial heartbeat adoption

The initial candidate blocked its own deployment because legacy production
cannot emit the new heartbeat. The Deploy input `approve_worker_bootstrap`
is an explicit, default-false initial-adoption authorization, separate from
production approval. It applies only to legacy SHA
`206a1a144be4e0c5de52238c273f89e908d4a84d` and an unchanged Alembic head.
Canonical dry-run still checks API readiness, API/Worker image parity,
rollback configuration, source identity, ADS=false and verified artifacts.
The collector confirms heartbeat absence using a successful Docker exec,
then requires the same running container, StartedAt, source and restart count
across 35 seconds, without OOM, pause or dead state. Docker read errors,
stale/malformed/present heartbeats or another source do not qualify.

The bootstrap receipt explicitly reports `primary_loop_verified=false`;
it proves stable legacy process state, not successful primary polling.
After switching, the strict candidate heartbeat and restart-delta gate is
mandatory, with no bootstrap parameter. Postdeploy failure leaves
`RECOVERY_REQUIRED`, never COMPLETE. A repeat bootstrap against the new
runtime is rejected. This is a containment release: 512MiB does not prove
that every potential memory leak has been fixed. Observe the next hourly
cycle and keep both advertising write flags false.

This procedure replaces the earlier statement that no reviewed bootstrap
exists. The old candidate remains blocked; a new exact SHA and successful
Prepare are required for the updated procedure.
