# Monthly Notation release coordinator

All three repositories have third-party dependencies. Among the Notation
repositories, `notation-go` consumes `notation-core-go`, and the CLI consumes
both libraries. Core does not depend on either of the other two Notation repos.
The coordinator uses those relationships, not an assumption that core has no
upstream libraries.

This fork-first implementation targets only `yizha1/notation-core-go`,
`yizha1/notation-go`, and `yizha1/notation`. It does not activate canonical
releases. Library and CLI versions remain independent.

```text
Monthly schedule or manual assessment
                 |
 notation-release-agent.md / generated .lock.yml
   Read-only evidence + Copilot assessment of all three repos
                 |
 Deterministic validation -> assessment artifact
                 |
 Explicitly approve a complete plan for a fork rehearsal
                 |
 notation-release-controller.yml + persistent cycle issue
                 |
 core worker -> verify public core package
                 |
 Go worker -> consume planned core through real Dependabot PR
                 |
 CLI worker -> consume planned libraries through real Dependabot PRs
                 |
 Verify downloaded CLI packages -> complete cycle
```

## Assessment and release decisions

The central agent runs at 09:00 UTC on the first day when
`NOTATION_RELEASE_AGENT_ENABLED=true`, or by manual dispatch. Before AI execution,
deterministic code collects exact main/release refs, latest canonical stable
baselines, proposed isolated tags, Dependabot PR heads/checks, unbackported
dependency commits, module manifests, and pinned source vulnerability scans.
Missing fork setup is recorded explicitly. Scanner/protocol failures fail
collection instead of producing clean evidence.

The agent returns `release`, `skip`, or `defer` for each repo with inventory
evidence references. It reviews compatibility and CVE fixes, but cannot merge,
sign, publish, configure credentials, or waive checks. The agent gets no release
credential or signing key. Its custom safe-output job validates the assessment
against the original pre-agent artifact and stores another artifact. Built-in
safe outputs are staged; the assessment does not write release state.

The controller verifies successful default-main agent-run provenance and
revalidates the artifact. Approving a plan containing deferrals is refused:
resolve the blockers and reassess first. The default controller mode is
`dry-run`; it cannot create state or dispatch release workers.

| Needed update | Release sequence |
|---------------|------------------|
| Core | Core, notation-go, CLI |
| notation-go only | notation-go, CLI |
| CLI only | CLI |
| No updates | Skip all three |
| Incomplete evidence or unavailable fix | Defer; do not approve |

Required consumers cannot be skipped. Unaffected repositories are skipped
without waiting for a nonexistent producer patch. Each included repository has
one reserved patch version for the cycle. There is no independent worker timer
or immediate downstream follow-up publisher.

## Ordered, resumable execution

An explicitly approved plan is stored in a trusted actor-authored cycle issue
in `yizha1/notation`. The controller invokes each included repository's existing
`monthly-patch-release.yml` in order. Worker code, workflow identity, PR heads,
baseline, isolated branches, tag, cycle, and dispatch attempt are checked.
Every writing worker stage validates its coordinator authorization.

The worker merges actual `dependabot[bot]` PRs to its isolated main branch only with successful
CI, a clean mergeable head, and satisfied required reviews. It uses normal
GitHub squash merges pinned to the observed head SHA. It never invokes an
administrative merge or resolves conflicts automatically.

After successful public package verification, the controller can dispatch the
next included repository. Consumer workers pin the plan's verified producer
versions rather than adopting arbitrary newer releases midway through the cycle.
New Dependabot propagation PRs may enter the fixed scope only when they update
the planned Notation producers without unrelated direct dependencies, replacements
or workflow changes. Indirect requirements and a producer-required Go floor may
change; module/tidy, minimum-Go and qualification gates still apply.
An unconsumed planned version waits for its genuine update PR and normal checks.
The workflow never manufactures a PR or edits dependency versions itself.

Workers notify the controller, not consumers. Every six hours the controller
also resumes existing approved state, including older unfinished months. It
does not automatically approve a new assessment. Each invocation exits after
advancing state or dispatching one worker; no runner waits for days.
Notifications are hints only; their payloads cannot approve plans or choose refs.

A failed gate blocks downstream dispatch. `retry_failed=true` explicitly
retries the same patch version. A changed assessed PR head or workflow commit
requires a fresh assessment and explicit `approve=true,replan=true`. Before
tagging, reassessment may refresh refs while retaining the DAG, reserved patch
versions, and completed releases. In-flight workers and already public
candidates cannot be replanned. Revisions and source run IDs remain in state.
An ambiguous dispatch is not blindly repeated; reconcile its recorded attempt.

The latest canonical stable SemVer release selects the `release-MAJOR.MINOR`
line, prefixed with `monthly-patch-test-` in a fork rehearsal. Dates and the
GitHub latest flag do not select the branch. Dependency-only commits are
backported with source provenance. A conflict, reverted backport, changed
baseline, or concurrent branch update stops the release. Source qualification
runs against an unpushed candidate bundle. The declared minimum Go minor is
tested with its latest patch, alongside stable Go.

Backport selection follows commits since main and the release branch diverged,
not the baseline's publication timestamp. Updates merged before a release was
published are still considered if they have not reached its release branch.
Already-present commits and recorded backports are not applied twice.

Only after source and artifact gates pass does an atomic, exact-lease push
advance the release branch and create the signed annotated patch tag. Tag
following is disabled. Tags are never moved or deleted by a retry.

Public release assets are downloaded and verified before the worker cycle is
marked `published`. A planned producer still awaiting verification blocks its
consumers. Empty local work is recorded as `skipped`; a required consumer cannot
skip after its producer publishes. Closed controller records prevent duplicate
monthly releases. An assessment with new updates cannot reopen a completed cycle.
If publication succeeds but a later test fails, the next run verifies the
same public release rather than cutting another version.

Planning all three repositories before publication reduces back-to-back patches:
consumers wait for known producer patches and include them in their one planned
release. Updates arriving after the approved plan are not silently added.
Urgent out-of-cycle security releases still require a separate maintainer
decision; this design cannot eliminate patches for genuinely later findings.

## Assessment-only setup and testing

Install the agent source/generated lock, controller and validation workflows
in the CLI fork. All three forks install the shared worker, helpers, tests,
and validation workflow. `.gitattributes` marks `.lock.yml` as generated.
Compile with pinned `gh-aw v0.89.21`; never hand-edit the lock file.

`notation-release-validation.yml` runs deterministic tests and actionlint on
`monthly-patch-test-code*` pushes and relevant pull requests. In the CLI fork it
also collects real read-only evidence without AI/release credentials. This can
test data collection and safety before enabling any release automation.

For hosted AI assessment on the personal fork, configure secret
`COPILOT_GITHUB_TOKEN` with a personal fine-grained token having account
permission **Copilot Requests: Read**. It is not the release-write credential.
Manual agent runs work without enabling the monthly schedule:

```sh
gh workflow run notation-release-agent.lock.yml --repo yizha1/notation --ref main
```

Inspect the run's `notation-release-inventory` and
`notation-release-assessment` artifacts. Preview it with the controller's
`assessment_run=<run ID>,mode=dry-run`. No issues, merges, tags, release assets,
or signing secrets are needed for assessment-only operation. Missing setup
should yield deferrals, not fabricated successful releases.

Issues are currently disabled in the three forks, and the CLI fork has no
Copilot Actions secret. These require separately authorized setup before
writing rehearsals and hosted AI execution, respectively.

## Writing rehearsal prerequisites

* `NOTATION_RELEASE_COORDINATOR_ENABLED=true` in the CLI fork. This enables
  resuming explicitly approved cycles, not automatic approval/publication of
  every agent assessment.
* `MONTHLY_PATCH_REHEARSAL_ENABLED=true` in each fork, and issues enabled in
  all three for trusted controller/worker state.
* Secret `MONTHLY_PATCH_TOKEN`: a dedicated GitHub App or fine-grained token
  scoped to the three forks. It needs contents, pull requests and issues write
  access, actions read/write for worker dispatch/status/artifacts, and contents
  write for controller wakeup dispatches. Do not give it branch-rule
  bypass rights. Unlike `GITHUB_TOKEN`, this credential lets normal merge
  events trigger CI and Dependabot follow-up activity.
* Secret `MONTHLY_PATCH_SIGNING_KEY`: a dedicated unencrypted SSH signing key.
  Store it only in GitHub Actions secrets. Register its public signing key
  with the declared GitHub signer before enabling execution.
* Variables `MONTHLY_PATCH_SIGNER_LOGIN`, `MONTHLY_PATCH_SIGNER_EMAIL`,
  optional `MONTHLY_PATCH_SIGNER_NAME`, and `MONTHLY_PATCH_ACTOR`. The actor
  must be the identity that creates cycle issues. All three repos must use
  that actor so consumers can authenticate producer cycle records.
* Reviewed isolated main/release branches, ordinary CI and compatible fork
  module requirements. Required protection/review rules are not bypassed.

Controller and writing workers execute only from assessed trusted default `main`.
Its read-only default token is used for downloaded-package verification.
Write credentials and signing material are not passed to source or native
package-test jobs, nor to push-triggered/no-write dry runs.

## Fork testing

The worker offers `dry-run` and coordinator-authorized `rehearse`. A push to a
`monthly-patch-test-code*` branch runs only a read-only dry run in a fork.
It cannot merge PRs, change branches, create tracking issues or publish tags.
A dry-run report can identify missing branch/setup prerequisites.

Seed reviewed dedicated branches before the writing assessment:

* `monthly-patch-test-main`
* `monthly-patch-test-release-MAJOR.MINOR`

Rehearsal mode refuses canonical repositories and never targets real `main`
or a real `release-*` branch. It publishes only
`vMAJOR.MINOR.PATCH-monthly-test.YYYYMM` prereleases. Their patch number is above
existing same-line fork tags, so previously tested versions cannot be moved
or confused with new rehearsal modules.

For real dependency propagation tests, consumer test branches must explicitly
track the fork producer using temporary Go `replace` directives. Canonical
requirements otherwise track `notaryproject`, not fork releases. Configure
Dependabot's `target-branch` for the isolated branches; GitHub reads its config
from the default branch, so installing that fork-only configuration needs a
separately approved default-branch/config change. Keep ordinary weekly version
checks so a producer tag can generate its consumer PR during the monthly window.
The dormant canonical engine rejects trial replacements. Canonical publishing
is not exposed by these fork coordinator workflows. Upstream adoption also
needs the existing CLI tag-publisher actor guard on main and its release branch.

Installing workflows, changing settings/secrets, or running a writing rehearsal
is a separate operation from preparing these files. Review exact outgoing
repository/branch scope before performing it.

## Workflow qualification and shipped CLI checks

Libraries retain module verification/tidy guards, vet, race tests, statement
coverage, pinned vulnerability scanning, and the inherited weak-compatible
license policy. CLI qualification covers root, E2E and plugin modules, and the
existing source E2E suite. License auditing includes all three manifests.

The CLI uses the trusted monthly GoReleaser config to build the same six
archives plus checksum manifest as ordinary releases, retaining symbol tables
without DWARF. Every binary's module/version/commit, architecture and hashes
are checked and scanned before tags or releases become public.

After publication, Linux AMD64, macOS ARM64 and Windows AMD64 jobs anonymously
download the actual published bytes. They check the recorded asset
digests/sizes/checksums and run the shipped executable, not a rebuilt CLI:

* Exact native version and commit output.
* Certificate generation, key/trust-store listing and trust-policy persistence.
* JWS and COSE signing and strict verification of an isolated OCI fixture.
* Rejection of unsigned artifacts and valid signatures with an untrusted identity.
* Full existing Linux E2E via `test/e2e/run.sh zot <downloaded-binary>`.

Failure is recorded in the cycle issue and retains test, scan and coverage
artifacts. Other matrix jobs are not cancelled by fail-fast.

Existing source tests remain release gates. Further runtime test-coverage
improvements are a separate follow-up, not part of this workflow change.
No arbitrary coverage percentage is used as a release waiver.

## Vulnerability dispositions

Scanner execution/protocol errors and reachable findings block by default.
JSON exit status alone never qualifies a scan. Complete raw, rendered and
summary evidence is retained. Informational module-only findings remain visible.

An optional `MONTHLY_PATCH_ADVISORY_DISPOSITIONS` variable accepts a reviewed
JSON list with `advisory`, `repository`, `line`, `owner`, `expires`, `modified`,
`reason` and `reference`. It is restricted to the actual monthly tag/repository/
release line, expires in UTC, and requires renewed review if the advisory
changes, is withdrawn, or gains a fixed version. Other findings still block.
No dispositions are installed or enabled by these workflow files.

Existing fork trial dispositions do not authorize future canonical releases.
Any approval for a monthly release-line disposition must be explicit and
recorded by its owner; published community guidance does not itself configure
a release scanner exception.
