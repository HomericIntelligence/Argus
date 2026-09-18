# Fleet activity observations

Argus reads Agamemnon's Fleet records and exposes **recently observed activity**
of distinct logical agents to Prometheus. Agamemnon owns admission, generation
and canonical task linkage; Hephaestus supplies worker observations. Argus does
not admit work, refresh observations, control workers or approve outputs.

The fixture contracts for this feature are:

- Agamemnon `580f1c320c5e7f0a5685cef78d7119cfccb0c7b1`: Fleet list,
  admission and observation fields. This dependency is unmerged at the feature's
  source binding; Fleet collection is disabled by default.
- Merged Hephaestus `ce29bd8732cceaadcd958d0b58586be81a4de73f`: event-driven
  activity refresh. These source bindings do not establish compatibility with a
  deployed service or a successful live worker demonstration.

## Enable collection for an exporter process

1. Confirm that the configured Agamemnon service supports all three Fleet list
   endpoints. Keep Fleet disabled until that dependency is available.
2. Supply the following settings through the native process environment or its
   existing supervisor. An operator must supply the API key through the existing
   protected configuration mechanism; do not put it in a URL, command example,
   log or metrics label.

   | Setting | Use |
   |---|---|
   | `FLEET_METRICS_ENABLED=true` | Explicit opt-in; the default is false. |
   | `AGAMEMNON_URL` | Existing Agamemnon HTTP(S) base URL, without userinfo, query or fragment. |
   | `AGAMEMNON_API_KEY` | Operator-supplied key for the Fleet Bearer header. A missing or invalid key makes Fleet unavailable. |
   | `AGAMEMNON_TLS_CA` | Existing optional CA bundle; otherwise use system trust. |
   | `TLS_VERIFY` | Keep verification enabled in production. The existing false setting remains an explicit development option. |

3. Apply the environment through the normal process startup/restart procedure.
   This feature does not change Compose, network topology or workflows.
4. Check `homeric_exporter_fleet_enabled` and the three
   `homeric_exporter_fleet_fetch_success` samples. Then check
   `hi_fleet_activity_complete` before interpreting activity counts. The
   [metric catalog](metrics.md#fleet-activity-observations) defines all families
   and fixed labels.

To disable collection, set `FLEET_METRICS_ENABLED=false` and apply the process
configuration. The enabled gauge remains present with value zero; other Fleet
samples and Fleet requests stop. Existing legacy metrics remain available.

## Read limits and failure behavior

With valid enabled configuration, each collection makes one authenticated GET
to each of `/v1/fleet/workers`, `/v1/fleet/sessions` and
`/v1/fleet/executions`. The key stays in requests to the configured Agamemnon
upstream; authenticated redirects are rejected.

At most three added requests run concurrently. Each has a five-second I/O
timeout; this is **not an absolute whole-scrape deadline**. Each response may
contain at most 1 MiB and 1,024 records. The `{items,total}` envelope must be
complete and internally consistent, with unique resource IDs. Failed reads,
invalid data and overflow are rejected without retries or truncation. There is
no earlier-success cache. Fleet configuration, fetch or classification failures
do not replace legacy collection with an error.

The three list reads are not an atomic snapshot. A change between reads can
make otherwise valid records inconsistent; Argus reports incomplete evidence
instead of selecting a favorable combination.

## What an activity count means

A qualifying session/execution record has claimed admission, an observed state,
a positive source sequence, a current matching worker generation, consistent
identity/lifecycle and activity `model_working` or `tool_running`. A pending
cancel/interrupt does not by itself establish that work has stopped.

Both worker `lastActivityAt` and controller `lastActivityReceivedAt` must be
timezone-aware, not future and at most 60 seconds old; exactly 60 seconds is
included. Fresh receipt cannot repair an old worker observation. Worker,
controller and exporter clock skew can make the classification unknown.

Hephaestus refreshes continuing activity on matching provider notifications,
at most once per five seconds; lifecycle observations can occur sooner. It has
**no periodic heartbeat guarantee**. Quiet work may continue past 60 seconds
without a new observation. That silence is unknown, not proof of idle work.
Worker time records observation processing, not provider occurrence; these fields
cannot measure delay while a notification waits in a queue.

Count distinct `agentId` values, not processes or rows. Two agents sharing a
runtime can count twice; consistent representations of one execution count once.
Conflicting admitted rows make classification incomplete, including different
owners claiming the same task or workspace. Supplied session and pool links must
agree with their related records. A valid canonical
`taskId` link selects `work_kind=issue`; its absence selects `interactive`.
That link alone does not verify GitHub issue eligibility.

| Observation set | Exposed result |
|---|---|
| Complete empty set or consistent inactive/nonactive records | Completeness 1; all four activity series are emitted, with zero where no agent qualifies. |
| Complete set with qualifying agents | Completeness 1; four series count distinct qualifying agents by activity and work kind. |
| Missing, malformed, stale, disconnected, hydrated, awaiting-observation, old-generation or inconsistent potentially admitted records | Completeness 0; all four activity count samples are omitted. |

Valid unclaimed/released records are expected inactive exclusions and alone do
not make completeness false. Fresh observed idle/waiting records do not earn
activity credit. Exclusions count records once per collection, not agents or
cumulative failures. Conflicting admitted groups take `ambiguous_agent`
precedence; otherwise classification checks structural/link validity, inactive
claims, worker/generation, reconciliation/observation state, sequence/lifecycle,
timestamps, then activity. A failed fetch does not invent excluded records.

## Query complete observations

Prometheus lookback can retain an older count after a later collection omits it.
Gate counts with the completeness value from the current scrape:

```promql
hi_fleet_recently_observed_active_agents
  and on(job, instance) (hi_fleet_activity_complete == 1)
```

Use the deployment's full scrape-target identity labels if they differ from
`job,instance`. Also check target scrape health (`up`) so a stopped exporter is
not mistaken for a current observation. Do not replace unavailable data with
`or vector(0)`.

If fetching fails, inspect the fixed resource success signals and protected
operator configuration. If fetching succeeds but completeness is zero, inspect
the fixed exclusion reasons. A later valid collection can recover without
reusing an earlier good count.

No task, prompt, key, workspace, agent, worker, host, pool, provider or event ID
is a Fleet metric label or diagnostic-log value. Tool activity does not prove
tool success. Provider completion, successful command acknowledgment and manual
resolution with `verifiedApproval=false` do not establish independently approved
completion. A first-worker acceptance demonstration still needs its separate
admission, activity/tool, output and independent review evidence; these gauges
do not establish that result or the 108-agent goal.
