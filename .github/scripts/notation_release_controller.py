# Copyright The Notary Project Authors.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Validate AI assessments and advance an explicitly approved fork release DAG."""

import argparse
import base64
import copy
import datetime
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import urllib.parse

import monthly_release as release
from monthly_release_checks import scan_messages

HOST = "yizha1/notation"
REPOSITORIES = tuple(f"yizha1/{name}" for name in release.PROJECTS)
AGENT_PATH = ".github/workflows/notation-release-agent.lock.yml"
WORKER_PATH = ".github/workflows/monthly-patch-release.yml"
DIGEST = re.compile(r"[0-9a-f]{64}$")
DECISIONS = {"release", "skip", "defer"}
NODE_STATUSES = {"pending", "dispatching", "running", "failed", "published", "skipped", "deferred"}
TERMINAL = {"published", "skipped"}


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON field: {key}")
        result[key] = value
    return result


def load_json(text):
    return json.loads(text, object_pairs_hook=unique_object)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def positive_id(value):
    if type(value) is not int or value <= 0:
        raise ValueError("Expected a positive integer identifier")
    return value


def fork_metadata(api, repository):
    if repository not in REPOSITORIES:
        raise ValueError("Coordinator is restricted to the three yizha1 forks")
    metadata = api.request(f"repos/{repository}")
    if not metadata.get("fork") or metadata.get("private") or metadata.get("default_branch") != "main":
        raise ValueError(f"Expected a public fork with trusted default main: {repository}")
    return metadata


def installed_worker(api, repository, commit):
    files = {
        WORKER_PATH: ("MONTHLY_PATCH_COORDINATOR_REQUIRED: 'true'", "  workflow_dispatch:"),
        ".github/scripts/notation_release_controller.py": ("def worker_authorization(",),
        ".github/scripts/monthly_release.py": ("coordinator_authorization(",),
        ".github/scripts/notation_fork_propagation.py": ("def prepare(", "def publish("),
    }
    for path, required in files.items():
        item = api.optional(f"repos/{repository}/contents/{path}?ref={commit}")
        if item is None:
            return False
        if item.get("encoding") != "base64":
            raise ValueError(f"Cannot inspect trusted worker installation: {path}")
        content = base64.b64decode(item["content"]).decode("utf-8")
        if any(token not in content for token in required):
            return False
    return True


def scan_source(repository, commit, directories, output):
    """Scan an immutable source tree without executing repository tests or hooks."""
    output.mkdir(parents=True, exist_ok=True)
    results = {}
    with tempfile.TemporaryDirectory(prefix="notation-release-assessment-") as temporary:
        tree = pathlib.Path(temporary)
        release.git(["init", "--quiet"], tree)
        release.git(["remote", "add", "origin", f"https://github.com/{repository}.git"], tree)
        release.git(["fetch", "--quiet", "--depth=1", "origin", commit], tree)
        release.git(["checkout", "--quiet", "--detach", "FETCH_HEAD"], tree)
        if release.git(["rev-parse", "HEAD"], tree) != commit:
            raise ValueError("Assessment source does not match its immutable commit")
        for index, directory in enumerate(directories):
            scanned = subprocess.run(
                ["govulncheck", "-format=json", "./..."], cwd=tree / directory,
                capture_output=True, text=True, encoding="utf-8",
            )
            (output / f"{index}.jsonstream").write_text(scanned.stdout, encoding="utf-8")
            (output / f"{index}.stderr.txt").write_text(scanned.stderr, encoding="utf-8")
            if scanned.returncode != 0:
                raise ValueError(f"Assessment scanner failed for {repository}:{directory}: {scanned.stderr}")
            reachable, informational, advisories = scan_messages(scanned.stdout, "source")
            results[directory] = {
                "reachable": sorted(reachable), "informational": sorted(informational),
                "advisories": {
                    key: {"summary": item.get("summary", ""), "modified": item.get("modified"),
                          "aliases": item.get("aliases", []),
                          "fixed": sorted({event["fixed"] for affected in item.get("affected", [])
                                           for ranges in affected.get("ranges", [])
                                           for event in ranges.get("events", []) if "fixed" in event})}
                    for key, item in advisories.items()
                },
            }
    return results


def compare_commits(api, repository, base, head):
    commits = []
    for page in range(1, 11):
        result = api.request(f"repos/{repository}/compare/{base}...{head}?per_page=100&page={page}")
        if result.get("status") not in {"ahead", "identical", "behind", "diverged"}:
            raise ValueError("Cannot establish main/release divergence")
        commits.extend(result["commits"])
        if len(commits) == result["ahead_by"]:
            if any(not release.SHA.fullmatch(item.get("sha", "")) for item in commits):
                raise ValueError("Invalid comparison commit")
            return {item["sha"] for item in commits}
        if len(result["commits"]) < 100:
            raise ValueError("Incomplete main/release comparison")
    raise ValueError("Assessment comparison exceeds 1,000 commits; manual reconciliation is required")


def collect(api, month, output):
    if not release.MONTH.fullmatch(month):
        raise ValueError("Invalid assessment month")
    inventory = {"schema": 1, "host": HOST, "month": month, "collected_at": utc_now(), "repositories": {}}
    for repository in REPOSITORIES:
        metadata = fork_metadata(api, repository)
        baseline = release.baseline_plan(api, repository, month, "rehearse")
        automation = api.request(f"repos/{repository}/branches/main")["commit"]["sha"]
        main = api.optional(f"repos/{repository}/branches/{baseline['main']}")
        branch = api.optional(f"repos/{repository}/branches/{baseline['branch']}")
        blockers = []
        if not metadata.get("has_issues"):
            blockers.append("Enable fork issues before any writing rehearsal")
        if main is None or branch is None:
            blockers.append("Reviewed isolated main/release branches are missing")
        workflow = api.optional(f"repos/{repository}/actions/workflows/monthly-patch-release.yml")
        if workflow is None or workflow.get("state") != "active" or not installed_worker(api, repository, automation):
            blockers.append("Install and enable the coordinator-aware release worker on fork main")
        source = repository if branch else f"notaryproject/{repository.split('/')[1]}"
        commit = branch["commit"]["sha"] if branch else api.request(
            f"repos/{source}/commits/{baseline['baseline']}"
        )["sha"]
        if not release.SHA.fullmatch(automation) or not release.SHA.fullmatch(commit):
            raise ValueError("Invalid source or automation commit")
        source_ref = main["commit"]["sha"] if main else automation
        pulls = []
        for pull in api.pulls(repository, baseline["main"] if main else "main", "open"):
            if not release.dependabot(pull, baseline["main"] if main else "main"):
                continue
            release.check_files(api, repository, pull["number"])
            ready, reason = release.checks_ready(pull, api.checks(repository, pull["number"]))
            pulls.append({"number": pull["number"], "head": pull["head"]["sha"],
                          "title": (pull.get("title") or "")[:400], "body": (pull.get("body") or "")[:1600],
                          "ready": ready, "reason": reason})
        merged = []
        if main and branch:
            ahead = compare_commits(api, repository, commit, source_ref)
            records = list(api.pages(f"repos/{repository}/commits?sha={commit}"))
            history = "\n".join(item["commit"]["message"] for item in records)
            for item in release.inventory(api, repository, baseline["main"]):
                if item["commit"] not in ahead:
                    continue
                backports = [record["sha"] for record in records
                             if f"(cherry picked from commit {item['commit']})" in record["commit"]["message"]]
                if backports:
                    if any(f"This reverts commit {backport}." in history for backport in backports):
                        blockers.append(f"Dependency backport #{item['number']} was reverted; manual review is required")
                    continue
                release.check_files(api, repository, item["number"])
                merged.append(item)
        scans = scan_source(source, commit, release.modules(repository), output / repository.split("/")[1])
        manifests = {directory: api.manifest(source, commit, directory) for directory in release.modules(repository)}
        inventory["repositories"][repository] = {
            "baseline": baseline["baseline"], "main": baseline["main"], "branch": baseline["branch"],
            "tag": baseline["tag"], "main_head": main["commit"]["sha"] if main else None,
            "release_head": branch["commit"]["sha"] if branch else None,
            "automation_sha": automation, "workflow_id": workflow["id"] if workflow else None,
            "source_repository": source, "source_commit": commit, "blockers": blockers,
            "pulls": pulls, "merged": merged, "manifests": manifests, "scans": scans,
        }
    return inventory


def evidence_ids(repository, node):
    return {f"{repository}:baseline", f"{repository}:setup", f"{repository}:dependencies",
            *(f"{repository}:pr:{item['number']}" for item in node["pulls"] + node["merged"]),
            *(f"{repository}:scan:{directory}" for directory in node["scans"])}


def validate_inventory(inventory):
    if type(inventory.get("schema")) is not int or inventory["schema"] != 1 or inventory.get("host") != HOST:
        raise ValueError("Unexpected assessment schema or host")
    if not release.MONTH.fullmatch(inventory.get("month", "")) or set(inventory.get("repositories", {})) != set(REPOSITORIES):
        raise ValueError("Incomplete assessment inventory")
    when = datetime.datetime.fromisoformat(inventory["collected_at"])
    if when.tzinfo is None:
        raise ValueError("Assessment timestamp must be timezone-aware")
    for repository, node in inventory["repositories"].items():
        release.validate_plan({
            "schema": 1, "repository": repository, "month": inventory["month"], "mode": "rehearse",
            **{key: node[key] for key in ("baseline", "main", "branch", "tag")}, "status": "merging",
        })
        for field in ("automation_sha", "source_commit"):
            if not release.SHA.fullmatch(node.get(field, "")):
                raise ValueError("Invalid inventory commit")
        for field in ("main_head", "release_head"):
            if node.get(field) is not None and not release.SHA.fullmatch(node[field]):
                raise ValueError("Invalid branch snapshot")
        if node["source_repository"] not in {repository, f"notaryproject/{repository.split('/')[1]}"}:
            raise ValueError("Assessment source escapes its project")
        if not isinstance(node["blockers"], list) or not all(isinstance(item, str) for item in node["blockers"]):
            raise ValueError("Invalid setup evidence")
        if set(node["manifests"]) != set(release.modules(repository)) or set(node["scans"]) != set(release.modules(repository)):
            raise ValueError("Incomplete module or vulnerability evidence")
        for result in node["scans"].values():
            for field in ("reachable", "informational"):
                if not isinstance(result[field], list) or any(not re.fullmatch(r"GO-\d{4}-\d+", item) for item in result[field]):
                    raise ValueError("Invalid vulnerability evidence")
        numbers = set()
        for kind, field in (("pulls", "head"), ("merged", "commit")):
            if not isinstance(node[kind], list):
                raise ValueError("Invalid dependency inventory")
            for item in node[kind]:
                number = positive_id(item["number"])
                if number in numbers or not release.SHA.fullmatch(item.get(field, "")):
                    raise ValueError("Duplicate or invalid dependency provenance")
                numbers.add(number)
        if node["workflow_id"] is not None:
            positive_id(node["workflow_id"])
    return inventory


def validate_assessment(inventory, assessment):
    validate_inventory(inventory)
    if set(assessment) != {"schema", "month", "inventory_sha256", "decisions"}:
        raise ValueError("Unexpected assessment fields")
    if type(assessment["schema"]) is not int or assessment["schema"] != 1 or assessment["month"] != inventory["month"] or assessment["inventory_sha256"] != digest(inventory):
        raise ValueError("Assessment is not bound to the collected inventory")
    if not isinstance(assessment["decisions"], dict) or set(assessment["decisions"]) != set(REPOSITORIES):
        raise ValueError("Agent must assess all three repositories exactly once")
    all_evidence = set().union(*(evidence_ids(repo, node) for repo, node in inventory["repositories"].items()))
    for repository, item in assessment["decisions"].items():
        if set(item) != {"decision", "reason", "evidence"} or item["decision"] not in DECISIONS:
            raise ValueError("Invalid release decision")
        if not isinstance(item["reason"], str) or not 1 <= len(item["reason"]) <= 2000:
            raise ValueError("Every decision needs a bounded rationale")
        if not isinstance(item["evidence"], list) or not 1 <= len(item["evidence"]) <= 30 or any(
            not isinstance(reference, str) or reference not in all_evidence for reference in item["evidence"]
        ):
            raise ValueError("Decision cites missing or fabricated evidence")
        if not set(item["evidence"]) & evidence_ids(repository, inventory["repositories"][repository]):
            raise ValueError("Decision needs evidence from its own repository")
        node = inventory["repositories"][repository]
        signals = bool(node["pulls"] or node["merged"] or any(result["reachable"] for result in node["scans"].values()))
        if item["decision"] == "skip" and (signals or node["blockers"]):
            raise ValueError("Known updates, CVEs or missing evidence cannot be skipped")
        if item["decision"] == "release" and node["blockers"]:
            raise ValueError("Repository setup is incomplete; defer rather than publish")
    decisions = copy.deepcopy(assessment["decisions"])
    affected = set()
    for repository in REPOSITORIES:
        node = inventory["repositories"][repository]
        upstream = [f"yizha1/{name}" for name in release.PROJECTS[repository.split("/")[1]]]
        required = any(producer in affected for producer in upstream)
        if required and decisions[repository]["decision"] == "skip":
            raise ValueError("A planned library update requires downstream inclusion")
        if decisions[repository]["decision"] == "release":
            if not (node["pulls"] or node["merged"] or required):
                raise ValueError("A CVE without a dependency fix requires deferral, not an empty patch")
            affected.add(repository)
        elif decisions[repository]["decision"] == "defer" and (
            node["pulls"] or node["merged"] or required or any(result["reachable"] for result in node["scans"].values())
        ):
            affected.add(repository)
    plan = {
        "schema": 1, "host": HOST, "month": inventory["month"], "inventory_sha256": digest(inventory),
        "collected_at": inventory["collected_at"], "decisions": decisions,
        "snapshots": {
            repo: {key: node[key] for key in ("baseline", "main", "branch", "tag", "main_head",
                                            "release_head", "automation_sha", "workflow_id", "pulls", "merged")}
            for repo, node in inventory["repositories"].items()
        },
    }
    # PR text helps the agent assess compatibility but is not executable authorization.
    for node in plan["snapshots"].values():
        node["pulls"] = [{"number": item["number"], "head": item["head"]} for item in node["pulls"]]
    plan["plan_id"] = digest(plan)
    return plan


def marker(month):
    if not release.MONTH.fullmatch(month):
        raise ValueError("Invalid controller month")
    return f"<!-- notation-release-controller:{month}:rehearse -->"


def validate_state(state):
    if type(state.get("schema")) is not int or state["schema"] != 1 or state.get("host") != HOST or state.get("mode") != "rehearse":
        raise ValueError("Invalid fork controller state")
    marker(state["month"])
    plan = state["plan"]
    if not DIGEST.fullmatch(plan.get("plan_id", "")) or digest({key: value for key, value in plan.items() if key != "plan_id"}) != plan["plan_id"]:
        raise ValueError("Approved plan changed")
    if plan.get("host") != HOST or type(plan.get("schema")) is not int or plan["schema"] != 1 or plan.get("month") != state["month"]:
        raise ValueError("Approved plan identity changed")
    if set(plan["decisions"]) != set(REPOSITORIES) or set(plan["snapshots"]) != set(REPOSITORIES) or set(state["nodes"]) != set(REPOSITORIES):
        raise ValueError("Controller repository scope changed")
    if state.get("status") not in {"active", "completed"}:
        raise ValueError("Invalid controller status")
    for repository, node in state["nodes"].items():
        decision = plan["decisions"][repository]["decision"]
        if decision not in DECISIONS or node["status"] not in NODE_STATUSES or type(node["attempt"]) is not int or node["attempt"] < 0:
            raise ValueError("Invalid controller node")
        if decision == "skip" and node["status"] != "skipped" or decision == "defer" and node["status"] != "deferred":
            raise ValueError("Agent decision changed during execution")
        snapshot = plan["snapshots"][repository]
        recovery = snapshot.get("verification_recovery")
        if recovery is not None and (
            not isinstance(recovery, dict) or set(recovery) != {"from_plan_id", "candidate_sha256"}
            or not all(isinstance(value, str) and DIGEST.fullmatch(value) for value in recovery.values())
            or decision != "release"
        ):
            raise ValueError("Invalid verification-only recovery receipt")
        release.validate_plan({"schema": 1, "repository": repository, "mode": "rehearse", "month": state["month"],
                               **{key: snapshot[key] for key in ("baseline", "main", "branch", "tag")}, "status": "merging"})
        if node.get("run_id") is not None:
            positive_id(node["run_id"])
        if node["status"] == "published" and (
            node.get("version") != snapshot["tag"] or not release.SHA.fullmatch(node.get("commit", ""))
        ):
            raise ValueError("Published producer identity changed")
    if state["status"] == "completed" and any(node["status"] not in TERMINAL for node in state["nodes"].values()):
        raise ValueError("Controller completed before its repositories")
    return state


def decode_issue(issue, actor):
    if "pull_request" in issue or not actor or issue.get("user", {}).get("login") != actor:
        return None
    body = issue.get("body") or ""
    if "<!-- notation-release-controller:" not in body:
        return None
    blocks = re.findall(r"```json\n(.*?)\n```", body, re.DOTALL)
    if len(blocks) != 1:
        raise ValueError("Malformed trusted controller record")
    state = validate_state(load_json(blocks[0]))
    if body.count(marker(state["month"])) != 1:
        raise ValueError("Controller marker changed")
    return state


class Controller:
    def __init__(self, api, actor):
        if not actor:
            raise ValueError("A configured controller actor is required")
        self.api, self.actor = api, actor

    def records(self):
        records = []
        for issue in self.api.pages(f"repos/{HOST}/issues?state=all"):
            state = decode_issue(issue, self.actor)
            if state is not None:
                records.append((issue, state))
        months = [state["month"] for _, state in records]
        if len(set(months)) != len(months) or sum(state["status"] == "active" for _, state in records) > 1:
            raise ValueError("Duplicate or overlapping controller cycles require reconciliation")
        return records

    def save(self, issue, state):
        validate_state(state)
        body = f"{marker(state['month'])}\nOutcome: **{state['status']}**.\n\n```json\n{json.dumps(state, indent=2)}\n```\n"
        if len(body.encode()) > 60000:
            raise ValueError("Controller record is too large")
        payload = {"body": body, "state": "closed" if state["status"] == "completed" else "open"}
        if issue:
            result = self.api.request(f"repos/{HOST}/issues/{issue['number']}", "PATCH", payload)
        else:
            payload["title"] = f"Notation fork release cycle: {state['month']}"
            result = self.api.request(f"repos/{HOST}/issues", "POST", payload)
        if decode_issue(result, self.actor) != state:
            raise ValueError("Controller state write was not confirmed by the trusted actor")
        return result

    def approve(self, plan, source_run, replan=False, recover_public_verification=False):
        if recover_public_verification and not replan:
            raise ValueError("Public verification recovery requires an explicit replan")
        if any(item["decision"] == "defer" for item in plan["decisions"].values()):
            raise ValueError("Resolve deferrals and reassess before approving a release cycle")
        records = self.records()
        for issue, state in records:
            if state["month"] == plan["month"]:
                if state["plan"]["plan_id"] != plan["plan_id"]:
                    if not replan:
                        raise ValueError("This month already has an immutable approved plan")
                    return self.replan(issue, state, plan, source_run, recover_public_verification)
                if recover_public_verification:
                    raise ValueError("Verification recovery requires a fresh changed assessment")
                return issue, state
            if state["status"] == "active":
                raise ValueError("Finish the active cycle before approving another month")
        if recover_public_verification:
            raise ValueError("Verification recovery requires an existing approved cycle")
        when = datetime.datetime.fromisoformat(plan["collected_at"])
        age = datetime.datetime.now(datetime.timezone.utc) - when
        if age < datetime.timedelta(0) or age > datetime.timedelta(days=7):
            raise ValueError("Assessment is stale; collect a fresh assessment before approval")
        for repository, item in plan["decisions"].items():
            if item["decision"] != "release":
                continue
            metadata = fork_metadata(self.api, repository)
            snapshot = plan["snapshots"][repository]
            if not metadata.get("has_issues"):
                raise ValueError("Worker cycle issues must be enabled before approval")
            for name, expected in ((snapshot["main"], snapshot["main_head"]),
                                   (snapshot["branch"], snapshot["release_head"]),
                                   ("main", snapshot["automation_sha"])):
                if self.api.request(f"repos/{repository}/branches/{name}")["commit"]["sha"] != expected:
                    raise ValueError("Assessed refs changed before approval")
        state = {
            "schema": 1, "host": HOST, "mode": "rehearse", "month": plan["month"],
            "plan": copy.deepcopy(plan), "source_run": positive_id(source_run), "status": "active",
            "nodes": {repo: {"status": {"release": "pending", "skip": "skipped", "defer": "deferred"}[item["decision"]],
                             "attempt": 0}
                      for repo, item in plan["decisions"].items()},
        }
        if all(node["status"] in TERMINAL for node in state["nodes"].values()):
            state["status"] = "completed"
        return self.save(None, state), state

    def replan(self, issue, state, plan, source_run, recover_public_verification=False):
        if state["status"] != "active" or any(node["status"] in {"dispatching", "running"} for node in state["nodes"].values()):
            raise ValueError("Cannot revise a completed cycle or an in-flight worker")
        when = datetime.datetime.fromisoformat(plan["collected_at"])
        age = datetime.datetime.now(datetime.timezone.utc) - when
        if age < datetime.timedelta(0) or age > datetime.timedelta(days=7):
            raise ValueError("Replacement assessment is stale")
        updated = copy.deepcopy(plan)
        recovered = False
        for repository, node in state["nodes"].items():
            previous = state["plan"]["snapshots"][repository]
            if node["status"] in TERMINAL:
                if updated["decisions"][repository]["decision"] != "skip":
                    raise ValueError("Reassessment cannot release an already completed repository again")
                updated["decisions"][repository] = state["plan"]["decisions"][repository]
                updated["snapshots"][repository] = previous
                continue
            if self.api.optional(f"repos/{repository}/git/ref/tags/{previous['tag']}") is not None:
                if not recover_public_verification:
                    raise ValueError("An immutable public candidate must be recovered, not replanned")
                cycle = release.Cycle(self.api, repository, state["month"], "rehearse")
                candidate = cycle.state
                if (candidate is None or candidate["status"] != "verifying"
                        or candidate.get("controller_issue") != issue["number"]
                        or candidate.get("controller_plan_id") != state["plan"]["plan_id"]
                        or any(candidate.get(key) != previous[key] for key in ("baseline", "main", "branch", "tag"))):
                    raise ValueError("Recovery requires this approved cycle's exact public verification candidate")
                snapshot = updated["snapshots"][repository]
                if (any(snapshot[key] != previous[key] for key in ("baseline", "main", "branch", "workflow_id"))
                        or snapshot["release_head"] != candidate["commit"]):
                    raise ValueError("Recovery cannot change the public candidate or its release branch")
                for name, expected in ((snapshot["main"], snapshot["main_head"]), (snapshot["branch"], candidate["commit"]),
                                       ("main", snapshot["automation_sha"])):
                    if self.api.request(f"repos/{repository}/branches/{name}")["commit"]["sha"] != expected:
                        raise ValueError("Verification recovery assessment refs are stale")
                release.assert_tag(self.api, candidate)
                release.check_public_assets(self.api, candidate)
                updated["decisions"][repository] = state["plan"]["decisions"][repository]
                updated["snapshots"][repository] = {
                    **previous, "automation_sha": snapshot["automation_sha"],
                    "verification_recovery": {
                        "from_plan_id": state["plan"]["plan_id"],
                        "candidate_sha256": digest({key: candidate[key] for key in ("tag", "commit", "assets")}),
                    },
                }
                recovered = True
                continue
            if updated["decisions"][repository]["decision"] != state["plan"]["decisions"][repository]["decision"]:
                raise ValueError("Reassessment may refresh refs, not silently change the release DAG")
            snapshot = updated["snapshots"][repository]
            if any(snapshot[key] != previous[key] for key in ("baseline", "main", "branch", "tag", "release_head")):
                raise ValueError("Reassessment changed the reserved patch or release baseline")
            for name, expected in ((snapshot["main"], snapshot["main_head"]), (snapshot["branch"], snapshot["release_head"]),
                                   ("main", snapshot["automation_sha"])):
                if self.api.request(f"repos/{repository}/branches/{name}")["commit"]["sha"] != expected:
                    raise ValueError("Replacement assessment refs are stale")
        if recover_public_verification and not recovered:
            raise ValueError("Verification recovery requires an existing immutable public candidate")
        previous_id = state["plan"]["plan_id"]
        updated["supersedes"] = previous_id
        updated["plan_id"] = digest({key: value for key, value in updated.items() if key != "plan_id"})
        revisions = state.setdefault("revisions", [])
        if len(revisions) >= 20:
            raise ValueError("Cycle revision limit reached")
        revisions.append({"from": previous_id, "to": updated["plan_id"], "source_run": positive_id(source_run)})
        for node in state["nodes"].values():
            if node["status"] not in TERMINAL:
                node["status"] = "pending"
                node.pop("run_id", None)
        state.update(plan=updated, source_run=source_run)
        return self.save(issue, state), state

    def advance(self, issue, state, retry_failed=False):
        validate_state(state)
        for repository in REPOSITORIES:
            node = state["nodes"][repository]
            if node["status"] in TERMINAL:
                continue
            producers = [f"yizha1/{name}" for name in release.PROJECTS[repository.split("/")[1]]]
            if node["status"] == "deferred" or any(state["nodes"][producer]["status"] not in TERMINAL for producer in producers):
                return issue, state
            snapshot = state["plan"]["snapshots"][repository]
            if node["status"] == "failed":
                if not retry_failed:
                    return issue, state
                node.pop("run_id", None)
                node["status"] = "pending"
            if node["status"] == "pending":
                metadata = fork_metadata(self.api, repository)
                if self.api.request(f"repos/{repository}/branches/{metadata['default_branch']}")["commit"]["sha"] != snapshot["automation_sha"]:
                    raise ValueError("Release worker code changed after assessment")
                worker = self.api.request(f"repos/{repository}/actions/workflows/monthly-patch-release.yml")
                if worker["id"] != snapshot["workflow_id"] or worker["state"] != "active":
                    raise ValueError("Assessed release worker changed or is disabled")
                node.update(status="dispatching", attempt=node["attempt"] + 1, dispatched_at=utc_now())
                issue = self.save(issue, state)
                self.api.request(f"repos/{repository}/actions/workflows/{worker['id']}/dispatches", "POST", {
                    "ref": "main", "inputs": {
                        "mode": "rehearse", "month": state["month"], "controller_issue": str(issue["number"]),
                        "plan_id": state["plan"]["plan_id"], "attempt": str(node["attempt"]),
                    },
                })
                return issue, state
            if node["status"] == "dispatching":
                candidates = []
                query = urllib.parse.urlencode({"event": "workflow_dispatch", "created": ">=" + node["dispatched_at"]})
                for page in range(1, 11):
                    runs = self.api.request(
                        f"repos/{repository}/actions/workflows/{snapshot['workflow_id']}/runs?{query}&per_page=100&page={page}"
                    )["workflow_runs"]
                    candidates.extend(run for run in runs if run.get("display_title") == run_title(state["month"], state["plan"]["plan_id"], issue["number"], node["attempt"]))
                    if len(runs) < 100:
                        break
                else:
                    raise ValueError("Incomplete dispatched-run discovery")
                if len(candidates) > 1:
                    raise ValueError("Duplicate dispatched workers require reconciliation")
                if not candidates:
                    # Do not re-dispatch an ambiguous request after a runner/network failure.
                    return issue, state
                run = candidates[0]
                if run.get("actor", {}).get("login") != self.actor or run.get("head_sha") != snapshot["automation_sha"] or run.get("event") != "workflow_dispatch":
                    raise ValueError("Worker provenance does not match the approved dispatch")
                node.update(status="running", run_id=positive_id(run["id"]))
                issue = self.save(issue, state)
            run = self.api.request(f"repos/{repository}/actions/runs/{node['run_id']}")
            if (run.get("actor", {}).get("login") != self.actor or run.get("workflow_id") != snapshot["workflow_id"]
                    or run.get("head_sha") != snapshot["automation_sha"] or run.get("event") != "workflow_dispatch"
                    or run.get("display_title") != run_title(state["month"], state["plan"]["plan_id"], issue["number"], node["attempt"])):
                raise ValueError("Recorded worker identity changed")
            cycle = release.Cycle(self.api, repository, state["month"], "rehearse")
            final = cycle.state is not None and cycle.state["status"] in release.FINAL
            if run["status"] != "completed" and not final:
                return issue, state
            if run["status"] == "completed" and run["conclusion"] != "success" and not final:
                node.update(status="failed", reason=f"Worker {node['run_id']} concluded {run['conclusion']}")
                return self.save(issue, state), state
            if cycle.state is None:
                # A blocked setup must never look like a successfully skipped patch.
                node.update(status="failed", reason="Worker returned without a trusted release cycle")
                return self.save(issue, state), state
            worker_plan = cycle.state
            if worker_plan.get("controller_plan_id") != state["plan"]["plan_id"] or worker_plan.get("controller_issue") != issue["number"]:
                raise ValueError("Worker cycle belongs to a different approved plan")
            if worker_plan["status"] not in release.FINAL:
                node.update(status="pending", reason=worker_plan.get("reason", "Worker is waiting for dependency checks"))
                node.pop("run_id", None)
                return self.save(issue, state), state
            if worker_plan["tag"] != snapshot["tag"]:
                raise ValueError("Worker changed its approved patch version")
            if worker_plan["status"] == "published":
                release.assert_tag(self.api, worker_plan)
                release.check_public_assets(self.api, worker_plan)
                expected = {producer.split("/")[1]: {"repository": producer, "version": state["nodes"][producer]["version"]}
                            for producer in producers if state["nodes"][producer]["status"] == "published"}
                if release.producer_lag(self.api, repository, worker_plan["commit"], "rehearse", expected):
                    raise ValueError("Published consumer does not include its planned producer versions")
                node.update(status="published", version=worker_plan["tag"], commit=worker_plan["commit"])
            else:
                if any(state["nodes"][producer]["status"] == "published" for producer in producers):
                    raise ValueError("A required consumer patch cannot be skipped after its producer released")
                node["status"] = "skipped"
            issue = self.save(issue, state)
        state["status"] = "completed"
        return self.save(issue, state), state


def run_title(month, plan_id, issue, attempt):
    return f"Monthly patch {month} {plan_id} #{issue} attempt {attempt}"


def worker_authorization(api, repository, month, plan=None, allow_previous=False, notification=False):
    if notification and plan is not None:
        raise ValueError("Controller progress notifications cannot authorize writing stages")
    if os.environ.get("MONTHLY_PATCH_COORDINATOR_REQUIRED") != "true":
        return None
    issue_number = os.environ.get("MONTHLY_PATCH_CONTROLLER_ISSUE", "")
    plan_id = os.environ.get("MONTHLY_PATCH_CONTROLLER_PLAN", "")
    attempt = os.environ.get("MONTHLY_PATCH_CONTROLLER_ATTEMPT", "")
    if not issue_number.isdecimal() or not attempt.isdecimal() or int(issue_number) <= 0 or int(attempt) <= 0 or not DIGEST.fullmatch(plan_id):
        raise ValueError("Writing workers require an approved coordinator receipt")
    fork_metadata(api, repository)
    issue = api.request(f"repos/{HOST}/issues/{issue_number}")
    state = decode_issue(issue, os.environ.get("MONTHLY_PATCH_ACTOR"))
    if state is None or state["month"] != month or state["status"] != "active" or state["plan"]["plan_id"] != plan_id:
        raise ValueError("Untrusted, stale or completed coordinator authorization")
    node = state["nodes"][repository]
    if state["plan"]["decisions"][repository]["decision"] != "release" or node["status"] not in {"dispatching", "running"} or node["attempt"] != int(attempt):
        raise ValueError("Coordinator did not dispatch this worker attempt")
    snapshot = state["plan"]["snapshots"][repository]
    if os.environ.get("GITHUB_SHA") != snapshot["automation_sha"] or os.environ.get("GITHUB_REF") != "refs/heads/main":
        raise ValueError("Writing worker must use the assessed default-branch code")
    recovery = snapshot.get("verification_recovery")
    if recovery is not None:
        candidate = release.Cycle(api, repository, month, "rehearse").state
        if (candidate is None or (
                candidate["status"] != "verifying" and not (notification and candidate["status"] == "published"))
                or candidate.get("controller_issue") != int(issue_number)
                or candidate.get("controller_plan_id") not in {plan_id, recovery["from_plan_id"]}
                or candidate["tag"] != snapshot["tag"]
                or digest({key: candidate[key] for key in ("tag", "commit", "assets")}) != recovery["candidate_sha256"]):
            raise ValueError("Verification recovery cannot change or rebuild its immutable public candidate")
        release.assert_tag(api, candidate)
        release.check_public_assets(api, candidate)
    producers = {}
    for name in release.PROJECTS[repository.split("/")[1]]:
        source = f"yizha1/{name}"
        upstream = state["nodes"][source]
        if upstream["status"] not in TERMINAL:
            raise ValueError("Producer verification has not completed")
        if upstream["status"] == "published":
            producers[name] = {"repository": source, "version": upstream["version"]}
    if plan is not None:
        if recovery is not None and (
            plan.get("status") != "verifying"
            or digest({key: plan.get(key) for key in ("tag", "commit", "assets")}) != recovery["candidate_sha256"]
        ):
            raise ValueError("A recovered public candidate authorizes verification only")
        allowed_ids = {plan_id}
        if allow_previous and plan.get("status") in {"merging", "waiting", "ready"}:
            allowed_ids.update(item["from"] for item in state.get("revisions", []))
        if allow_previous and recovery is not None and plan.get("status") == "verifying":
            if digest({key: plan[key] for key in ("tag", "commit", "assets")}) == recovery["candidate_sha256"]:
                allowed_ids.add(recovery["from_plan_id"])
        if (plan.get("mode") != "rehearse" or plan.get("cycle_id", "monthly") != "monthly"
                or plan.get("controller_issue") != int(issue_number) or plan.get("controller_plan_id") not in allowed_ids
                or any(plan.get(key) != snapshot[key] for key in ("baseline", "main", "branch", "tag"))
                or plan.get("producers", {}) != producers):
            raise ValueError("Worker plan escapes its fixed coordinator authorization")
    return {"issue": int(issue_number), "plan_id": plan_id, "snapshot": snapshot, "producers": producers}


def module_version(value):
    match = re.fullmatch(
        r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
        r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?",
        value if isinstance(value, str) else "",
    )
    if match is None:
        raise ValueError("Invalid producer module version")
    major, minor, patch, prerelease = match.groups()
    identifiers = []
    for identifier in prerelease.split(".") if prerelease is not None else ():
        if identifier.isdecimal():
            if len(identifier) > 1 and identifier.startswith("0"):
                raise ValueError("Invalid numeric producer prerelease identifier")
            identifiers.append((0, int(identifier)))
        else:
            identifiers.append((1, identifier))
    return int(major), int(minor), int(patch), prerelease is None, tuple(identifiers)


def producer_manifests(before_modules, after_modules, producers, producer_modules, current):
    changed = False
    allowed = {f"github.com/notaryproject/{name}" for name in producers}
    def go_version(value):
        if not isinstance(value, str) or not re.fullmatch(r"\d+\.\d+(?:\.\d+)?", value):
            raise ValueError("Invalid producer Go requirement")
        return tuple(map(int, value.split("."))) + (0,) * (3 - len(value.split(".")))
    producer_go = {name: go_version(manifest["Go"]) for name, manifest in producer_modules.items()}
    for directory, before in before_modules.items():
        after = after_modules[directory]
        changed_go = []
        changed_producers = []
        for name, item in producers.items():
            module = f"github.com/notaryproject/{name}"
            def dependency(manifest):
                return ([entry for entry in manifest.get("Require") or [] if entry["Path"] == module],
                        [entry for entry in manifest.get("Replace") or [] if entry["Old"]["Path"] == module])
            if dependency(before) != dependency(after):
                if release.requirement(after, module, item["repository"], "rehearse") != item["version"]:
                    return False
                changed = True
                changed_go.append(producer_go[name])
                changed_producers.append(name)
        floor = max([go_version(before["Go"]), *changed_go])
        if before["Go"] != after["Go"] and go_version(after["Go"]) != floor:
            return False
        if before.get("Toolchain") != after.get("Toolchain"):
            chain = after.get("Toolchain") or ""
            if not isinstance(chain, str) or not chain.startswith("go"):
                return False
            if not floor <= go_version(chain.removeprefix("go")) <= go_version(current.removeprefix("go")):
                return False
        required = {}
        for name in changed_producers:
            for entry in producer_modules[name].get("Require") or []:
                path, version = entry["Path"], entry["Version"]
                if path not in required or module_version(version) > module_version(required[path]):
                    required[path] = version
        original = {entry["Path"]: entry for entry in before.get("Require") or []}
        updated = {entry["Path"]: entry for entry in after.get("Require") or []}
        for path, previous in original.items():
            if path not in allowed and not previous.get("Indirect") and path in required:
                if (module_version(required[path]) > module_version(previous["Version"])
                        and updated.get(path, {}).get("Version") != required[path]):
                    return False
        def unrelated(manifest, normalize=False):
            result = copy.deepcopy(manifest)
            # MVS can change indirect requirements when a producer is updated.
            result["Require"] = [item for item in result.get("Require") or [] if item["Path"] not in allowed and not item.get("Indirect")]
            for entry in result["Require"]:
                path = entry["Path"]
                previous = original.get(path)
                if (normalize and previous is not None and path in required
                        and entry["Version"] == required[path]
                        and module_version(required[path]) > module_version(previous["Version"])):
                    entry["Version"] = previous["Version"]
            result["Replace"] = [item for item in result.get("Replace") or [] if item["Old"]["Path"] not in allowed]
            result.pop("Go", None)
            result.pop("Toolchain", None)
            return result
        if unrelated(before) != unrelated(after, normalize=True):
            return False
    return changed


def producer_pull(api, repository, pull, producers):
    """Permit new propagation PRs, but not unrelated changes hidden in grouped updates."""
    if not producers or (pull["head"].get("repo") or {}).get("full_name") != repository:
        return False
    files = list(api.pages(f"repos/{repository}/pulls/{pull['number']}/files"))
    paths = {f"{directory}/go.{extension}".removeprefix("./") for directory in release.modules(repository)
             for extension in ("mod", "sum")}
    if not files or any(item["filename"] not in paths or (item.get("previous_filename") and item["previous_filename"] not in paths) for item in files):
        return False
    before = {directory: api.manifest(repository, pull["base"]["sha"], directory) for directory in release.modules(repository)}
    after = {directory: api.manifest(repository, pull["head"]["sha"], directory) for directory in release.modules(repository)}
    producer_modules = {name: api.manifest(item["repository"], item["version"], ".") for name, item in producers.items()}
    current = release.run(["go", "env", "GOVERSION"]).strip()
    return producer_manifests(before, after, producers, producer_modules, current)


def propagation_branch(month, plan_id):
    if not release.MONTH.fullmatch(month) or not DIGEST.fullmatch(plan_id):
        raise ValueError("Invalid fork propagation identity")
    return f"monthly-patch-test-propagation/{month}/{plan_id}"


def fork_propagation_pull(api, repository, pull, authorization):
    if repository not in REPOSITORIES or not authorization or not authorization["producers"]:
        return False
    if pull.get("user", {}).get("login") != os.environ.get("MONTHLY_PATCH_ACTOR"):
        return False
    blocks = re.findall(r"```json\n(.*?)\n```", pull.get("body") or "", flags=re.DOTALL)
    if len(blocks) != 1:
        return False
    receipt = load_json(blocks[0])
    if set(receipt) != {"schema", "repository", "month", "controller_issue", "plan_id", "producers", "base", "head"}:
        return False
    if (type(receipt["schema"]) is not int or receipt["schema"] != 1 or receipt["repository"] != repository
            or receipt["controller_issue"] != authorization["issue"] or receipt["plan_id"] != authorization["plan_id"]
            or receipt["producers"] != authorization["producers"] or not release.MONTH.fullmatch(receipt["month"])
            or not authorization["snapshot"]["tag"].endswith("-monthly-test." + receipt["month"].replace("-", ""))
            or not release.SHA.fullmatch(receipt["base"]) or not release.SHA.fullmatch(receipt["head"])
            or pull["base"]["ref"] != authorization["snapshot"]["main"]
            or (pull["head"].get("repo") or {}).get("full_name") != repository
            or pull["head"].get("ref") != propagation_branch(receipt["month"], receipt["plan_id"])
            or pull["head"]["sha"] != receipt["head"]):
        return False
    marker = f"<!-- notation-fork-propagation:{receipt['month']}:{receipt['plan_id']} -->"
    if marker not in (pull.get("body") or ""):
        return False
    commit = api.request(f"repos/{repository}/commits/{receipt['head']}")
    if not commit["commit"]["verification"]["verified"] or (commit.get("author") or {}).get("login") != os.environ["MONTHLY_PATCH_ACTOR"]:
        raise ValueError("Fork propagation PR requires a verified commit by the configured actor")
    scoped = copy.deepcopy(pull)
    scoped["base"]["sha"] = receipt["base"]
    return producer_pull(api, repository, scoped, authorization["producers"])


def scoped_pull(api, repository, pull, authorization, merged=()):
    snapshot = authorization["snapshot"]
    heads = {item["number"]: item["head"] for item in snapshot["pulls"]}
    commits = {item["number"]: item["commit"] for item in snapshot["merged"]}
    number = pull["number"]
    approved_merges = {item["number"]: item["commit"] for item in merged}
    if number in approved_merges and pull.get("merged_at"):
        if pull.get("merge_commit_sha") != approved_merges[number]:
            raise ValueError("Worker-approved dependency merge changed")
        return True
    if number in heads:
        if pull["head"]["sha"] != heads[number]:
            raise ValueError("An assessed Dependabot PR head changed; reassessment is required")
        return True
    if number in commits:
        if pull.get("merge_commit_sha") != commits[number]:
            raise ValueError("Assessed dependency backport provenance changed")
        return True
    if pull.get("user", {}).get("login") != "dependabot[bot]":
        return fork_propagation_pull(api, repository, pull, authorization)
    return producer_pull(api, repository, pull, authorization["producers"])


def trusted_assessment(api, run_id, output):
    run_id = positive_id(run_id)
    run = api.request(f"repos/{HOST}/actions/runs/{run_id}")
    workflow = api.request(f"repos/{HOST}/actions/workflows/notation-release-agent.lock.yml")
    metadata = fork_metadata(api, HOST)
    if (run.get("workflow_id") != workflow["id"] or run.get("path") != AGENT_PATH
            or run.get("head_branch") != metadata["default_branch"] or run.get("head_repository", {}).get("full_name") != HOST
            or run.get("event") not in {"schedule", "workflow_dispatch"} or run.get("conclusion") != "success"):
        raise ValueError("Assessment must come from a successful default-branch fork agent run")
    artifacts = api.request(f"repos/{HOST}/actions/runs/{run_id}/artifacts?per_page=100")
    if artifacts["total_count"] > 100:
        raise ValueError("Unexpected assessment artifact count")
    matches = [item for item in artifacts["artifacts"] if item["name"] == "notation-release-assessment" and not item["expired"]]
    if len(matches) != 1:
        raise ValueError("Missing or duplicate validated assessment artifact")
    artifact = matches[0]
    if artifact.get("workflow_run", {}).get("head_sha") != run["head_sha"]:
        raise ValueError("Assessment artifact provenance changed")
    release.run(["gh", "run", "download", str(run_id), "--repo", HOST, "--name", artifact["name"], "--dir", str(output)])
    envelope = load_json((output / "assessment.json").read_text())
    expected = validate_assessment(envelope["inventory"], envelope["assessment"])
    if expected != envelope["plan"]:
        raise ValueError("Validated assessment was modified")
    return expected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["collect", "validate", "control"])
    parser.add_argument("--month", default=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m"))
    parser.add_argument("--inventory", type=pathlib.Path)
    parser.add_argument("--agent-output", type=pathlib.Path)
    parser.add_argument("--assessment-run", type=int)
    parser.add_argument("--mode", choices=["dry-run", "rehearse"], default="dry-run")
    parser.add_argument("--approve", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--replan", action="store_true")
    parser.add_argument("--recover-public-verification", action="store_true")
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    if args.recover_public_verification and not (args.assessment_run and args.approve and args.replan and args.mode == "rehearse"):
        parser.error("Public verification recovery requires a fresh assessment, rehearse mode, approval and replan")
    try:
        args.output.mkdir(parents=True, exist_ok=True)
        api = release.GitHub()
        if args.command == "collect":
            inventory = collect(api, args.month, args.output / "scans")
            validate_inventory(inventory)
            (args.output / "inventory.json").write_text(json.dumps(inventory, indent=2) + "\n")
            (args.output / "inventory.sha256").write_text(digest(inventory) + "\n")
            result = {"inventory_sha256": digest(inventory), "month": args.month, "writes": False}
        elif args.command == "validate":
            inventory = load_json(args.inventory.read_text())
            items = load_json(args.agent_output.read_text())["items"]
            if len(items) != 1 or items[0].get("type") != "submit_release_plan":
                raise ValueError("Agent must submit exactly one structured assessment")
            assessment = load_json(items[0]["assessment_json"])
            plan = validate_assessment(inventory, assessment)
            result = {"inventory": inventory, "assessment": assessment, "plan": plan}
            (args.output / "assessment.json").write_text(json.dumps(result, indent=2) + "\n")
        else:
            if os.environ.get("GITHUB_REPOSITORY") != HOST or os.environ.get("GITHUB_REF") != "refs/heads/main":
                raise ValueError("Controller must run from the fork's trusted default main")
            if args.mode != "dry-run" and (
                os.environ.get("NOTATION_RELEASE_COORDINATOR_ENABLED") != "true"
                or not os.environ.get("MONTHLY_PATCH_ACTOR")
                or os.environ.get("MONTHLY_PATCH_TOKEN_READY") != "true"
            ):
                raise ValueError("Writing controller requires explicit opt-in and its scoped credential")
            if args.assessment_run is not None:
                plan = trusted_assessment(api, args.assessment_run, args.output / "download")
                if args.mode == "dry-run":
                    result = {"plan": plan, "writes": False}
                elif not args.approve:
                    raise ValueError("New fork release plans require explicit approval")
                else:
                    controller = Controller(api, os.environ["MONTHLY_PATCH_ACTOR"])
                    issue, state = controller.approve(plan, args.assessment_run, args.replan, args.recover_public_verification)
                    issue, state = controller.advance(issue, state, args.retry_failed)
                    result = {"issue": issue["number"], "state": state}
            elif args.mode == "dry-run":
                result = {"writes": False, "reason": "Supply an assessment run to preview its validated plan"}
            else:
                controller = Controller(api, os.environ["MONTHLY_PATCH_ACTOR"])
                records = [(issue, state) for issue, state in controller.records() if state["status"] == "active"]
                if records:
                    issue, state = controller.advance(*records[0], retry_failed=args.retry_failed)
                    result = {"issue": issue["number"], "state": state}
                else:
                    result = {"reason": "No approved active cycle to resume"}
        (args.output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result, indent=2))
        return 0
    except (ValueError, KeyError, TypeError, OSError, RuntimeError) as error:
        print(f"Notation release coordinator failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
