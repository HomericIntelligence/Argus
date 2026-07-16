# Merge Queue

Argus prepares GitHub Actions for a repository-level merge queue on `main`, but
the queue is intentionally activated in a separate operator step. This staged
rollout implements [Argus#550](https://github.com/HomericIntelligence/Argus/issues/550)
under the policy approved in
[Odysseus#386](https://github.com/HomericIntelligence/Odysseus/issues/386).

## Required-check contract

Two active repository rulesets supply the merge-blocking status-check contract:

- `homeric-main-baseline`: `lint`, `unit-tests`, `integration-tests`,
  `security/dependency-scan`, `security/secrets-scan`, `config-validate`,
  `schema-validation`, `deps/version-sync`, `test`, `package`, `install`,
  `release`, and `build`.
- `homeric-main-extras`: `Validate configs`.

The `Required Checks` and `CI` workflows emit these contexts. Both workflows
must retain their existing `pull_request` and `push` behavior and must run for
`merge_group` events of type `checks_requested`. GitHub evaluates the same
required contexts on the synthetic merge-group SHA; the queue cannot advance if
either workflow fails to run.

## Approved queue policy

The activation step must add exactly this rule to a repository ruleset that
targets only `refs/heads/main`:

```json
{
  "type": "merge_queue",
  "parameters": {
    "check_response_timeout_minutes": 60,
    "grouping_strategy": "ALLGREEN",
    "max_entries_to_build": 10,
    "max_entries_to_merge": 5,
    "merge_method": "SQUASH",
    "min_entries_to_merge": 1,
    "min_entries_to_merge_wait_minutes": 5
  }
}
```

This rule layers on top of the existing required-status-check rules. It does
not replace, rename, or remove any required context or other protection.

## Post-merge activation

Do not activate before all of the following are true:

1. The workflow and validation changes from Argus issue #550 are merged to
   `main`.
2. Both required workflows are present on `main` with the
   `merge_group/checks_requested` trigger.
3. An operator is ready to run and observe one representative queued PR through
   the complete check cycle.

Argus does not have a canonical repository-owned ruleset automation file. The
operator must therefore create a dedicated active repository ruleset named
`homeric-main-merge-queue`, targeting only `refs/heads/main`. Mirror the live
`homeric-main-baseline` bypass actors so queue activation does not change bypass
behavior. At the issue #550 baseline, that is repository role `5` in
`pull_request` mode. Re-read the baseline immediately before activation and
stop if it has drifted. A dedicated ruleset leaves both existing rulesets
unchanged and makes rollback independent of the required-check contract.

The equivalent create-ruleset payload is:

```json
{
  "name": "homeric-main-merge-queue",
  "target": "branch",
  "enforcement": "active",
  "bypass_actors": [
    {
      "actor_id": 5,
      "actor_type": "RepositoryRole",
      "bypass_mode": "pull_request"
    }
  ],
  "conditions": {
    "ref_name": {
      "exclude": [],
      "include": ["refs/heads/main"]
    }
  },
  "rules": [
    {
      "type": "merge_queue",
      "parameters": {
        "check_response_timeout_minutes": 60,
        "grouping_strategy": "ALLGREEN",
        "max_entries_to_build": 10,
        "max_entries_to_merge": 5,
        "merge_method": "SQUASH",
        "min_entries_to_merge": 1,
        "min_entries_to_merge_wait_minutes": 5
      }
    }
  ]
}
```

Save the payload to `/tmp/argus-merge-queue-ruleset.json`, then create the
ruleset with `POST /repos/HomericIntelligence/Argus/rulesets` only after the
activation gates above are satisfied:

```bash
gh api --method POST repos/HomericIntelligence/Argus/rulesets \
  --input /tmp/argus-merge-queue-ruleset.json
```

Immediately read back the effective rules on `main` and confirm:

- the merge-queue parameters exactly match the approved policy;
- all 14 required contexts listed above remain present across the two existing
  rulesets;
- required review-thread resolution and every unrelated protection still
  apply; and
- a queued smoke PR produces both required workflow runs on a `merge_group`
  event and merges by squash only after all required checks pass.

If read-back or the smoke cycle fails, disable the dedicated
`homeric-main-merge-queue` ruleset. Do not edit the two existing required-check
rulesets as part of rollback. Record the live ruleset response, workflow event,
and queued merge result on Argus issue #550.
