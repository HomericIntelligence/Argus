# Merge Queue

Argus is workflow-ready for a repository merge queue on `main`, but queue
activation and queued smoke evidence are intentionally post-merge operator
steps. This staged rollout implements
[Argus #550](https://github.com/HomericIntelligence/Argus/issues/550).

The workflow changes in this rollout modify `.github/workflows/` and require
independent human review before merge. Local tests and automated review do not
replace that approval.

## Required-check contract

Argus currently has 14 required contexts across two existing active repository
rulesets:

- `homeric-main-baseline`: `lint`, `unit-tests`, `integration-tests`,
  `security/dependency-scan`, `security/secrets-scan`, `config-validate`,
  `schema-validation`, `deps/version-sync`, `test`, `package`, `install`,
  `release`, and `build`.
- `homeric-main-extras`: `Validate configs`.

The `Required Checks` and `CI` workflows emit these contexts. Both workflows
retain their existing `pull_request` and `push` behavior and also run for the
`merge_group` event type `checks_requested`. GitHub evaluates required contexts
on the synthetic merge-group SHA, so the queue cannot advance unless every
supplying workflow runs and succeeds.

The dedicated repository ruleset `homeric-main-merge-queue` is the sole
authority for Argus queue policy. It layers a `merge_queue` rule on top of the
existing protections; it must not replace, rename, or remove either existing
ruleset or any required context.

## Cross-repository automation boundary

[Odysseus #386](https://github.com/HomericIntelligence/Odysseus/issues/386) is
the durable rollout umbrella, and
[Odysseus PR #417](https://github.com/HomericIntelligence/Odysseus/pull/417) is
the current central activation implementation. Until that implementation is
merged and independently verified, generic baseline replacement must not target
Argus. Any central activation path must preserve Argus's two existing rulesets,
all 14 contexts, bypass actors, and unrelated protections while creating or
updating only `homeric-main-merge-queue`.

This Argus change does not complete the cross-repository rollout. Keep the
umbrella issue open until Odysseus PR #417 and every repository activation have
independent evidence, including verified Argus behavior.

## Approved queue policy

The dedicated ruleset must target only `refs/heads/main` and contain exactly
this queue rule:

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

## Post-merge activation

Do not activate the queue until all of these gates are satisfied:

1. The workflow and validation changes from Argus #550 are merged to `main`.
2. An independent human has reviewed the `.github/workflows/` changes.
3. Both workflows exist on `main` with the
   `merge_group/checks_requested` trigger.
4. An operator is ready to observe a representative queued PR through the
   complete required-check cycle.
5. The activation path from Odysseus PR #417 has been independently verified
   for Argus before it is run against this repository.

### 1. Look up and snapshot the exact existing rulesets

Run steps 1 through 5 in the same trusted Bash shell so the guarded IDs and
snapshots carry forward. The shell must provide `gh`, `jq`, and `sha256sum`:

```bash
set -euo pipefail

REPO=HomericIntelligence/Argus
BASELINE_NAME=homeric-main-baseline
EXTRAS_NAME=homeric-main-extras
QUEUE_NAME=homeric-main-merge-queue
RULESET_LIST=/tmp/argus-rulesets.before.json

gh api "repos/${REPO}/rulesets?includes_parents=false" > "${RULESET_LIST}"

for name in "${BASELINE_NAME}" "${EXTRAS_NAME}"; do
  jq -e --arg name "${name}" \
    '[.[] | select(.source_type == "Repository" and .name == $name)] | length == 1' \
    "${RULESET_LIST}" >/dev/null
done

jq -e --arg name "${QUEUE_NAME}" \
  '[.[] | select(.source_type == "Repository" and .name == $name)] | length == 0' \
  "${RULESET_LIST}" >/dev/null

BASELINE_ID=$(jq -r --arg name "${BASELINE_NAME}" \
  '.[] | select(.source_type == "Repository" and .name == $name) | .id' \
  "${RULESET_LIST}")
EXTRAS_ID=$(jq -r --arg name "${EXTRAS_NAME}" \
  '.[] | select(.source_type == "Repository" and .name == $name) | .id' \
  "${RULESET_LIST}")

gh api "repos/${REPO}/rulesets/${BASELINE_ID}" \
  > /tmp/argus-baseline.before.json
gh api "repos/${REPO}/rulesets/${EXTRAS_ID}" \
  > /tmp/argus-extras.before.json

jq -e --arg name "${BASELINE_NAME}" '
  .name == $name
  and .target == "branch"
  and .enforcement == "active"
  and (.conditions.ref_name.include | index("refs/heads/main")) != null' \
  /tmp/argus-baseline.before.json >/dev/null
jq -e --arg name "${EXTRAS_NAME}" '
  .name == $name
  and .target == "branch"
  and .enforcement == "active"
  and (.conditions.ref_name.include | index("refs/heads/main")) != null' \
  /tmp/argus-extras.before.json >/dev/null

sha256sum /tmp/argus-baseline.before.json /tmp/argus-extras.before.json
```

Stop if either named ruleset is missing, duplicated, inactive, or no longer
targets `refs/heads/main`. Do not repair drift as part of queue activation.

### 2. Verify the effective required contexts before activation

This checks the rules GitHub actually applies to `main`, not just a payload in
a runbook:

```bash
set -euo pipefail

gh api "repos/${REPO}/rules/branches/main" \
  > /tmp/argus-main-effective-rules.before.json

jq -n '$ARGS.positional | unique | sort' --args \
  "Validate configs" \
  build \
  config-validate \
  deps/version-sync \
  install \
  integration-tests \
  lint \
  package \
  release \
  schema-validation \
  security/dependency-scan \
  security/secrets-scan \
  test \
  unit-tests \
  > /tmp/argus-required-contexts.expected.json

jq '[.[] | select(.type == "required_status_checks")
     | .parameters.required_status_checks[]?.context] | unique | sort' \
  /tmp/argus-main-effective-rules.before.json \
  > /tmp/argus-required-contexts.before.json

jq -e 'length == 14' /tmp/argus-required-contexts.before.json >/dev/null
diff -u /tmp/argus-required-contexts.expected.json \
  /tmp/argus-required-contexts.before.json
```

Stop on any difference. Activation must not weaken or rename protection.

### 3. Build the dedicated ruleset payload from live bypass actors

Create the new ruleset disabled first. Derive bypass actors from the live
baseline rather than copying a role ID from documentation:

```bash
jq '{
  name: "homeric-main-merge-queue",
  target: "branch",
  enforcement: "disabled",
  bypass_actors: .bypass_actors,
  conditions: {
    ref_name: {
      exclude: [],
      include: ["refs/heads/main"]
    }
  },
  rules: [
    {
      type: "merge_queue",
      parameters: {
        check_response_timeout_minutes: 60,
        grouping_strategy: "ALLGREEN",
        max_entries_to_build: 10,
        max_entries_to_merge: 5,
        merge_method: "SQUASH",
        min_entries_to_merge: 1,
        min_entries_to_merge_wait_minutes: 5
      }
    }
  ]
}' /tmp/argus-baseline.before.json \
  > /tmp/argus-merge-queue-ruleset.create.json

gh api --method POST "repos/${REPO}/rulesets" \
  --input /tmp/argus-merge-queue-ruleset.create.json \
  > /tmp/argus-merge-queue-ruleset.created.json

QUEUE_ID=$(jq -er '.id' /tmp/argus-merge-queue-ruleset.created.json)
```

Do not use a baseline-replacement script or send a `PUT` to either existing
ruleset.

### 4. Read back the exact dedicated ruleset, then activate it

```bash
gh api "repos/${REPO}/rulesets/${QUEUE_ID}" \
  > /tmp/argus-merge-queue-ruleset.readback.json

jq -e --arg name "${QUEUE_NAME}" '
  .name == $name
  and .target == "branch"
  and .enforcement == "disabled"
  and .conditions.ref_name.exclude == []
  and .conditions.ref_name.include == ["refs/heads/main"]
  and (.rules | length) == 1
  and .rules[0] == {
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
  }' /tmp/argus-merge-queue-ruleset.readback.json >/dev/null

jq -S '.bypass_actors' /tmp/argus-baseline.before.json \
  > /tmp/argus-baseline-bypass.json
jq -S '.bypass_actors' /tmp/argus-merge-queue-ruleset.readback.json \
  > /tmp/argus-queue-bypass.json
diff -u /tmp/argus-baseline-bypass.json /tmp/argus-queue-bypass.json

gh api --method PUT "repos/${REPO}/rulesets/${QUEUE_ID}" \
  -f enforcement=active \
  > /tmp/argus-merge-queue-ruleset.activated.json

```

Do not trust the PUT response as final evidence. Continue directly to step 5
and re-read the live policy before attempting a smoke PR.

### 5. Re-read the exact queue policy and prove protections were preserved

```bash
gh api "repos/${REPO}/rulesets/${QUEUE_ID}" \
  > /tmp/argus-merge-queue-ruleset.after.json

jq -e --arg id "${QUEUE_ID}" --arg name "${QUEUE_NAME}" '
  .id == ($id | tonumber)
  and .name == $name
  and .source_type == "Repository"
  and .target == "branch"
  and .enforcement == "active"
  and .conditions.ref_name.exclude == []
  and .conditions.ref_name.include == ["refs/heads/main"]
  and (.rules | length) == 1
  and .rules[0] == {
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
  }' /tmp/argus-merge-queue-ruleset.after.json >/dev/null

jq -S '.bypass_actors' /tmp/argus-baseline.before.json \
  > /tmp/argus-baseline-bypass.after.json
jq -S '.bypass_actors' /tmp/argus-merge-queue-ruleset.after.json \
  > /tmp/argus-queue-bypass.after.json
diff -u /tmp/argus-baseline-bypass.after.json \
  /tmp/argus-queue-bypass.after.json

gh api "repos/${REPO}/rulesets/${BASELINE_ID}" \
  > /tmp/argus-baseline.after.json
gh api "repos/${REPO}/rulesets/${EXTRAS_ID}" \
  > /tmp/argus-extras.after.json

diff -u \
  <(jq -S . /tmp/argus-baseline.before.json) \
  <(jq -S . /tmp/argus-baseline.after.json)
diff -u \
  <(jq -S . /tmp/argus-extras.before.json) \
  <(jq -S . /tmp/argus-extras.after.json)

gh api "repos/${REPO}/rules/branches/main" \
  > /tmp/argus-main-effective-rules.after.json
jq '[.[] | select(.type == "required_status_checks")
     | .parameters.required_status_checks[]?.context] | unique | sort' \
  /tmp/argus-main-effective-rules.after.json \
  > /tmp/argus-required-contexts.after.json

jq -e 'length == 14' /tmp/argus-required-contexts.after.json >/dev/null
diff -u /tmp/argus-required-contexts.expected.json \
  /tmp/argus-required-contexts.after.json
```

Only after every exact post-PUT assertion above succeeds may smoke testing
begin. Then enqueue one representative smoke PR. Record the PR URL and the
verbatim `merge_group` workflow/check results on Argus #550. Do not claim
activation complete until all 14 required contexts report on the merge-group
SHA and the PR merges by squash.

## Safe rollback

Rollback must address only `homeric-main-merge-queue`. Never disable, update,
or delete `homeric-main-baseline` or `homeric-main-extras` to recover the queue.

### Disable the dedicated queue ruleset

```bash
set -euo pipefail

REPO=HomericIntelligence/Argus
QUEUE_NAME=homeric-main-merge-queue
gh api "repos/${REPO}/rulesets?includes_parents=false" \
  > /tmp/argus-rulesets.rollback.json

jq -e --arg name "${QUEUE_NAME}" \
  '[.[] | select(.source_type == "Repository" and .name == $name)] | length == 1' \
  /tmp/argus-rulesets.rollback.json >/dev/null
QUEUE_ID=$(jq -r --arg name "${QUEUE_NAME}" \
  '.[] | select(.source_type == "Repository" and .name == $name) | .id' \
  /tmp/argus-rulesets.rollback.json)

test "$(gh api "repos/${REPO}/rulesets/${QUEUE_ID}" --jq '.name')" \
  = "${QUEUE_NAME}"
gh api --method PUT "repos/${REPO}/rulesets/${QUEUE_ID}" \
  -f enforcement=disabled \
  > /tmp/argus-merge-queue-ruleset.disabled.json
test "$(jq -r '.enforcement' /tmp/argus-merge-queue-ruleset.disabled.json)" \
  = disabled
```

Re-run the effective-context verification in step 5. All 14 contexts must
remain present after rollback.

### Optionally delete the disabled dedicated ruleset

Deletion is optional; leaving the dedicated ruleset disabled preserves an
audit trail. If deletion is required, use the freshly looked-up ID and require
an exact-name confirmation:

```bash
test "$(gh api "repos/${REPO}/rulesets/${QUEUE_ID}" --jq '.name')" \
  = "${QUEUE_NAME}"
test "$(gh api "repos/${REPO}/rulesets/${QUEUE_ID}" --jq '.enforcement')" \
  = disabled

printf 'Type %s to delete only the disabled queue ruleset: ' "${QUEUE_NAME}" >&2
read -r CONFIRM
test "${CONFIRM}" = "${QUEUE_NAME}"
gh api --method DELETE "repos/${REPO}/rulesets/${QUEUE_ID}"

test "$(gh api "repos/${REPO}/rulesets?includes_parents=false" \
  --jq ".[] | select(.name == \"${QUEUE_NAME}\") | .id" | wc -l)" -eq 0
```

After disable or delete, record the exact read-back and effective-context
results on Argus #550.
