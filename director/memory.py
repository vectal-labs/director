"""JSONL review history and the scan's repeat-review and sampling rules."""
import datetime as dt
import json
import math
import pathlib
import random

try:
    from . import preferences
    from .storage import ROOT, PROFILE, STATE, LEGACY
except ImportError:
    import preferences
    from storage import ROOT, PROFILE, STATE, LEGACY

LOG = STATE / "log.jsonl"
SCANS = STATE / "scans"
DECISIONS = ("unblock", "leave", "wait_for_david", "deny")
OUTCOMES = ("sent", "queued", "failed", "skipped")
LESSON_KINDS = ("general_preference", "project_decision", "temporary_instruction", "exception")


def lesson_deadline(value):
    if not isinstance(value, str):
        raise ValueError("expires_at must be an ISO timestamp with a timezone")
    try:
        date = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("expires_at must be an ISO timestamp with a timezone") from error
    if date.tzinfo is None:
        raise ValueError("expires_at must include a timezone")
    return date.timestamp()


def validate_lesson(lesson, bound=False):
    fields = {"kind", "scope", "interpretation", "reason", "applies_when", "ends_when", "expires_at"}
    if bound:
        fields |= {"id", "app", "target"}
    if not isinstance(lesson, dict) or set(lesson) - fields:
        raise ValueError("lesson must be an object containing only the documented fields")
    if lesson.get("kind") not in LESSON_KINDS:
        raise ValueError("unknown lesson kind")
    scope = lesson.get("scope", "thread")
    if scope not in {"general", "project", "thread"}:
        raise ValueError("lesson scope must be general, project, or thread")
    for field in ("interpretation", "reason", "applies_when"):
        if not isinstance(lesson.get(field), str) or not lesson[field].strip():
            raise ValueError(f"lesson {field} must be nonblank text")
    if lesson["kind"] == "project_decision" and scope != "project":
        raise ValueError("a project decision needs project scope")
    if lesson["kind"] == "exception" and scope != "thread":
        raise ValueError("an exception stays within its original thread/session")
    if "ends_when" in lesson and (not isinstance(lesson["ends_when"], str) or not lesson["ends_when"].strip()):
        raise ValueError("ends_when must describe a real ending condition")
    if "expires_at" in lesson:
        lesson_deadline(lesson["expires_at"])
    if lesson["kind"] == "temporary_instruction" and not (lesson.get("ends_when") or lesson.get("expires_at")):
        raise ValueError("a temporary instruction needs ends_when or expires_at")
    if bound:
        if not {"id", "scope", "app", "target"}.issubset(lesson):
            raise ValueError("stored lesson needs its explicit scope binding")
        if not isinstance(lesson.get("id"), str) or not lesson["id"].strip():
            raise ValueError("stored lesson needs an id")
        if scope == "general":
            if lesson.get("app") is not None or lesson.get("target") is not None:
                raise ValueError("general lessons cannot carry a project/thread binding")
        elif (lesson.get("app") not in {"bb", "cmux"}
              or not isinstance(lesson.get("target"), str) or not lesson["target"].strip()):
            raise ValueError("stored lesson needs an app and a stable scope target")


def new_lesson(value, review, existing_ids=()):
    """Bind scope to recorded evidence, never a model-supplied target or title."""
    validate_lesson(value)
    lesson = dict(value, scope=value.get("scope", "thread"))
    scope = lesson["scope"]
    app, target = None, None
    if scope != "general":
        app = review.get("app")
        if app not in {"bb", "cmux"}:
            raise ValueError("scoped lessons require a review recorded with --scan")
        if scope == "project":
            target = review.get("project")
        else:
            target = review.get("review_key") if app == "cmux" else review["picked"]
        if not target:
            raise ValueError("the original review lacks a verified scope target; record a new review with --scan")
    number = len(review.get("corrections", [])) + 1
    while f"{review['run']}.{number}" in existing_ids:
        number += 1
    lesson.update(id=f"{review['run']}.{number}", app=app, target=target)
    validate_lesson(lesson, bound=True)
    return lesson


def lesson_corrections(runs):
    for review in runs:
        for correction in review.get("corrections", []):
            if correction.get("lesson"):
                yield review, correction


def find_lesson(runs, lesson_id):
    for review, correction in lesson_corrections(runs):
        if correction["lesson"]["id"] == lesson_id:
            return review, correction
    raise ValueError(f"no lesson {lesson_id}")


def attach_lessons(candidates, runs, now, app, records=None):
    """Select possible precedents; the model must still check applies_when."""
    if records is None:
        records = [{**correction["lesson"], "david": correction["david"], "run": review["run"],
                    "rule": correction.get("rule"), "recorded_at": correction.get("ts"),
                    "lesson_ended": correction.get("lesson_ended")}
                   for review, correction in lesson_corrections(runs)]
    active = [{key: value for key, value in lesson.items() if key != "lesson_ended"}
              for lesson in records if not lesson.get("lesson_ended")
              and not (lesson.get("expires_at") and now >= lesson_deadline(lesson["expires_at"]))]
    for candidate in candidates:
        matches = []
        for lesson in active:
            scope = lesson["scope"]
            target = candidate.get("project") if scope == "project" else (
                candidate.get("review_key") if app == "cmux" else candidate["id"])
            if scope == "general" or (lesson["app"] == app and lesson["target"] == target):
                matches.append(lesson)
        candidate["lessons"] = matches


def read_priorities(path=None):
    """Load optional personal priorities; reject mistakes instead of ignoring them."""
    path = pathlib.Path(path) if path is not None else PROFILE / "priorities.json"
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"projects": {}, "threads": {}}
    except (OSError, ValueError) as error:
        raise ValueError(f"cannot read priorities file {path}: {error}") from error
    if not isinstance(config, dict) or set(config) - {"projects", "threads"}:
        raise ValueError(f"{path}: expected an object with only projects and threads")
    for group in ("projects", "threads"):
        weights = config.setdefault(group, {})
        if not isinstance(weights, dict):
            raise ValueError(f"{path}: {group} must be an object mapping IDs to weights")
        for key, weight in weights.items():
            if not key.strip():
                raise ValueError(f"{path}: {group} IDs must not be empty")
            if (type(weight) not in (int, float) or weight <= 0
                    or (isinstance(weight, float) and not math.isfinite(weight))):
                raise ValueError(f"{path}: {group}.{key} must be a finite number greater than zero")
    return config


def priority(row, config):
    """Thread overrides replace project weights; names and titles are not keys."""
    for group, key, source in (("threads", row.get("review_key") or row["id"], "thread"),
                               ("projects", row.get("project"), "project")):
        if key in config.get(group, {}):
            return {"priority_weight": config[group][key], "priority_source": source, "priority_key": key}
    return {"priority_weight": 1.0, "priority_source": "default", "priority_key": None}


def timestamp():
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def effective_decision(row, settings=None):
    correction = row.get("david_override")
    if not correction:
        return row["decision"]
    # Some historical corrections only recorded a rule ID. Its meaning belongs to the profile.
    decisions = (settings or preferences.DEFAULTS).get("legacy_override_decisions", {})
    return correction.get("decision") or decisions.get(correction.get("rule"))


def apply_outcome(review, event):
    """Fold an action event without creating a review or moving its cooldown."""
    allowed = {"none": {"skipped"}, "proposed": set(OUTCOMES),
               "sent": {"sent", "failed", "resumed"}, "queued": {"queued", "sent", "failed", "resumed"}}
    before, after = review.get("action_status", "unknown"), event["status"]
    if after not in allowed.get(before, set()):
        raise ValueError(f"cannot change action from {before} to {after}; start a new review for a new attempt")
    dt.datetime.fromisoformat(event["ts"])
    if not isinstance(event.get("detail"), str) or not event["detail"].strip():
        raise ValueError("an outcome needs its observed result")
    if after == "resumed":
        observed = event.get("observation") or {}
        if not isinstance(observed, dict):
            raise ValueError("invalid resume observation")
        app = review.get("app")
        same_target = (observed.get("review_key") == review.get("review_key") and bool(review.get("review_key"))
                       if app == "cmux" else observed.get("id") == review["picked"])
        running_status = {"bb": "active", "cmux": "running"}.get(app)
        if not running_status or observed.get("status") != running_status or not same_target:
            raise ValueError("a confirmed resume needs a running observation of the reviewed agent")
        dt.datetime.fromisoformat(observed["observed_at"])
    review.update(action_status=after, outcome=event)
    if event.get("action") is not None:
        review["action"] = event["action"]


def read(text=None):
    if text is None:
        text = LOG.read_text() if LOG.exists() else ""
    runs, lesson_ids = {}, set()
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            run = row["run"]
            if row.get("kind") == "lesson_end":
                review, correction = find_lesson(list(runs.values()), row["lesson_id"])
                if (review["run"] != run or correction.get("lesson_ended")
                        or not isinstance(row.get("evidence"), str) or not row["evidence"].strip()):
                    raise ValueError("invalid lesson ending")
                dt.datetime.fromisoformat(row["ts"])
                correction["lesson_ended"] = row
            elif row.get("kind") == "outcome":
                apply_outcome(runs[run], row)
            elif row.get("kind") == "override":
                if row["decision"] not in DECISIONS:
                    raise ValueError("invalid correction")
                if "lesson" in row:
                    validate_lesson(row["lesson"], bound=True)
                    if (row["lesson"]["id"] in lesson_ids or not isinstance(row.get("david"), str)
                            or not row["david"].strip()):
                        raise ValueError("invalid lesson source")
                    lesson_ids.add(row["lesson"]["id"])
                runs[run]["corrections"].append(row)
                runs[run]["david_override"] = row
            else:
                if run in runs or row["decision"] not in DECISIONS:
                    raise ValueError("invalid run")
                row["picked"]
                dt.datetime.fromisoformat(row["ts"])
                if row.get("action_status", "unknown") not in {"none", "proposed", "unknown"}:
                    raise ValueError("action results must follow the original review")
                # Legacy inline overrides are evidence, with no inferred lesson scope.
                row["corrections"] = [row["david_override"]] if row.get("david_override") else []
                runs[run] = row
        except (ValueError, KeyError, TypeError) as error:
            raise ValueError(f"log.jsonl line {number}: invalid record") from error
    return list(runs.values())


def histories(runs, settings=None):
    result = {}
    for row in runs:
        picked = row.get("review_key") or row["picked"]
        count = result.get(picked, {}).get("pick_count", 0) + 1
        result[picked] = {
            "pick_count": count, "last_run": row["run"],
            "last_review_at": row.get("reviewed_at", row["ts"]),
            "last_decision": effective_decision(row, settings),
            "last_override": row.get("david_override"),
            "reviewed_state": row.get("reviewed_state"),
        }
    return result


def rank(candidates, runs, now, seed, priorities=None, settings=None):
    settings = preferences.validate(settings or {})
    recheck_seconds = settings["recheck_seconds"]
    spot_check_chance = settings["spot_check_chance"]
    history = histories(runs, settings)
    for row in candidates:
        row.update(priority(row, priorities or {}))
        previous = history.get(row.get("review_key") or row["id"])
        elapsed, changed, reason = None, None, "never_reviewed"
        if previous:
            elapsed = max(0, now - dt.datetime.fromisoformat(previous["last_review_at"]).timestamp())
            before = previous["reviewed_state"]
            changed = before != row["state"] if before else None
            if changed is None:
                reason = "state_unknown"
            elif changed:
                reason = "state_changed"
            elif previous["last_decision"] != "leave":
                reason = "needs_review"
            else:
                reason = "leave_expired" if elapsed >= recheck_seconds else "leave_cooldown"
        if row.get("input_history_known") is False:
            reason = "input_history_unknown"
        row.update(history=previous or {"pick_count": 0}, changed_since_review=changed,
                   review_eligible=reason not in {"leave_cooldown", "input_history_unknown"}, eligibility_reason=reason,
                   review_age_min=round(elapsed / 60, 2) if elapsed is not None else None,
                   recheck_in_sec=max(0, recheck_seconds - elapsed) if reason == "leave_cooldown" else 0,
                   spot_check=False)
        # Linear age in minutes, capped at one day; unseen threads get the cap.
        row["random_weight"] = max(1, min(1440, elapsed / 60)) if elapsed is not None else 1440
    candidates.sort(key=lambda r: (not r["review_eligible"], not r["pending_interaction"],
                                   r["recent_error"] is None, not r["ends_with_question"],
                                   -r["priority_weight"], r["idle_min"], r["id"]))
    eligible = [r for r in candidates if r["review_eligible"]]
    rng = random.Random(seed)
    draw = rng.random()
    selection = {"seed": seed, "draw": draw, "chance": spot_check_chance,
                 "mode": "none", "suggested": None, "eligible_count": len(eligible)}
    if eligible:
        spot = draw < spot_check_chance
        picked = rng.choices(eligible, weights=[r["random_weight"] for r in eligible])[0] if spot else eligible[0]
        picked["spot_check"] = spot
        selection.update(mode="spot_check" if spot else "priority", suggested=picked["id"])
    return selection
