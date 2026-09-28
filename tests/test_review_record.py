"""The required ``review record`` status: a record counts only for the head it names.

``scripts/review_record.py`` holds the decision; the workflow step in
``.github/workflows/review-record.yml`` is run here with a fake ``gh`` on the
PATH, so the shell around the decision is tested too.
"""

from __future__ import annotations

import importlib.util
import itertools
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "review_record.py"
WORKFLOW = ROOT / ".github" / "workflows" / "review-record.yml"

HEAD = "0123456789abcdef0123456789abcdef01234567"
OLD = "fedcba9876543210fedcba9876543210fedcba98"
HEADING = "## Independent review record"


@pytest.fixture(scope="module")
def rr():
    spec = importlib.util.spec_from_file_location("review_record", SCRIPT)
    assert spec
    assert spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["review_record"] = module
    spec.loader.exec_module(module)
    return module


def _comment(body, association="OWNER", comment_id=1):
    return {"id": comment_id, "author_association": association, "body": body}


def _record(sha, extra=""):
    return f"{HEADING}\n\nVerdict: approve.\n\nReviewed head: {sha}\n{extra}"


# -- decide -------------------------------------------------------------------


def test_no_comment_at_all_fails(rr):
    state, text = rr.decide([], HEAD)
    assert state == "failure"
    assert "Reviewed head:" in text


def test_a_comment_that_is_not_a_record_fails(rr):
    assert rr.decide([_comment(f"LGTM\n\nReviewed head: {HEAD}")], HEAD)[0] == "failure"


def test_a_record_for_the_current_head_passes(rr):
    state, text = rr.decide([_comment(_record(HEAD), comment_id=42)], HEAD)
    assert state == "success"
    assert "42" in text
    assert HEAD[:7] in text


def test_a_record_for_an_older_head_fails_after_a_push(rr):
    state, text = rr.decide([_comment(_record(OLD))], HEAD)
    assert state == "failure"
    assert HEAD[:12] in text


def test_a_record_without_a_reviewed_head_line_fails(rr):
    assert rr.decide([_comment(f"{HEADING}\n\nAll good.")], HEAD)[0] == "failure"


@pytest.mark.parametrize("association", ["OWNER", "MEMBER", "COLLABORATOR"])
def test_the_trusted_associations_count(rr, association):
    assert rr.decide([_comment(_record(HEAD), association)], HEAD)[0] == "success"


@pytest.mark.parametrize(
    "association", ["CONTRIBUTOR", "FIRST_TIME_CONTRIBUTOR", "FIRST_TIMER", "NONE", "", None]
)
def test_a_record_from_anyone_else_does_not_count(rr, association):
    assert rr.decide([_comment(_record(HEAD), association)], HEAD)[0] == "failure"


@pytest.mark.parametrize(
    "body",
    [
        f"Re: {HEADING}\n\nReviewed head: {HEAD}",
        f" {HEADING}\n\nReviewed head: {HEAD}",
        f"\n{HEADING}\n\nReviewed head: {HEAD}",
        f"# Independent review record\n\nReviewed head: {HEAD}",
    ],
)
def test_the_heading_must_start_the_comment(rr, body):
    assert rr.decide([_comment(body)], HEAD)[0] == "failure"


def test_the_heading_may_carry_a_suffix(rr):
    body = f"{HEADING} (re-review)\n\nReviewed head: {HEAD}\n"
    assert rr.decide([_comment(body)], HEAD)[0] == "success"


def test_an_uppercase_sha_in_the_record_matches(rr):
    assert rr.decide([_comment(_record(HEAD.upper()))], HEAD)[0] == "success"


def test_an_uppercase_head_matches_a_lowercase_record(rr):
    assert rr.decide([_comment(_record(HEAD))], HEAD.upper())[0] == "success"


def test_the_sha_may_be_in_backticks(rr):
    assert rr.decide([_comment(_record(f"`{HEAD}`"))], HEAD)[0] == "success"


def test_windows_line_endings_are_accepted(rr):
    body = _record(HEAD).replace("\n", "\r\n")
    assert rr.decide([_comment(body)], HEAD)[0] == "success"


@pytest.mark.parametrize(
    "line",
    [
        f"The fixes are in {HEAD}.",  # in prose, no label
        f"Reviewed head: {HEAD} and the next commit",  # trailing text
        f"Reviewed heads: {HEAD}",
        f"reviewed head: {HEAD}",
        f"- Reviewed head: {HEAD}",
        f"Reviewed head: {HEAD[:7]}",  # a prefix is ambiguous
        f"Reviewed head: {HEAD[:39]}",
        f"Reviewed head: {HEAD}0",  # 41 hex digits
        f"Reviewed head: 0{HEAD}",
        f"Reviewed head: `{HEAD[:7]}`",
        f"Reviewed head: `{HEAD}0`",
        f"Reviewed head: `{HEAD}",  # unbalanced backtick
        f"Reviewed head: {HEAD[:39]}g",
        f"Reviewed head:{HEAD}",
    ],
)
def test_a_sha_embedded_in_other_text_does_not_count(rr, line):
    body = f"{HEADING}\n\n{line}\n"
    assert rr.decide([_comment(body)], HEAD)[0] == "failure"


@pytest.mark.parametrize(
    "body",
    [
        f"{HEADING}\n\n```\nReviewed head: {HEAD}\n```\n",
        f"{HEADING}\n\n```text\nReviewed head: {HEAD}\n```\n",
        f"{HEADING}\n\n~~~\nReviewed head: {HEAD}\n~~~\n",
        f"{HEADING}\n\n````\n```\nReviewed head: {HEAD}\n````\n",  # a shorter fence is text
        f"{HEADING}\n\n```\n~~~\nReviewed head: {HEAD}\n```\n",  # the other fence is text
        f"{HEADING}\n\n```\n``` x\nReviewed head: {HEAD}\n```\n",  # a closer has no text
        f"{HEADING}\n\n  ```\nReviewed head: {HEAD}\n",  # an unclosed fence runs to the end
        f"{HEADING}\n\n<!--\nReviewed head: {HEAD}\n-->\n",
        f"{HEADING}\n\n<!-- Reviewed head: {HEAD} -->\n",
        f"{HEADING}\n\n<!-- note\nReviewed head: {HEAD}\n",  # an unclosed comment too
        f"{HEADING}\n\n    Reviewed head: {HEAD}\n",  # indented code
        f"{HEADING}\n\n\tReviewed head: {HEAD}\n",
    ],
)
def test_a_line_in_code_or_an_html_comment_does_not_count(rr, body):
    assert rr.decide([_comment(body)], HEAD)[0] == "failure"


def test_a_line_after_a_closed_fence_or_comment_counts(rr):
    body = f"{HEADING}\n\n```\nReviewed head: {OLD}\n```\n<!-- x -->\nReviewed head: {HEAD}\n"
    assert rr.decide([_comment(body)], HEAD)[0] == "success"
    assert rr.decide([_comment(body)], OLD)[0] == "failure"


@pytest.mark.parametrize("separator", ["\u2028", "\u2029", "\x85", "\x0b", "\x0c", "\x1c"])
def test_only_a_newline_breaks_a_line(rr, separator):
    body = f"{HEADING}\n\nThe fix is in.{separator}Reviewed head: {HEAD}\n"
    assert rr.decide([_comment(body)], HEAD)[0] == "failure"
    body = f"{HEADING}\n\n{separator}Reviewed head: {HEAD}\n"
    assert rr.decide([_comment(body)], HEAD)[0] == "failure"


def test_surrounding_spaces_on_the_line_are_accepted(rr):
    body = f"{HEADING}\n\n  Reviewed head: {HEAD}  \n"
    assert rr.decide([_comment(body)], HEAD)[0] == "success"


def test_one_record_for_the_current_head_among_older_ones_passes(rr):
    comments = [
        _comment(_record(OLD), comment_id=1),
        _comment("Addressed the findings.", comment_id=2),
        _comment(f"{HEADING} (re-review)\n\nReviewed head: {HEAD}\n", comment_id=3),
    ]
    state, text = rr.decide(comments, HEAD)
    assert state == "success"
    assert "comment 3" in text


def test_a_current_record_from_an_untrusted_author_does_not_rescue_a_stale_one(rr):
    comments = [
        _comment(_record(OLD), comment_id=1),
        _comment(_record(HEAD), "NONE", comment_id=2),
    ]
    assert rr.decide(comments, HEAD)[0] == "failure"


def test_a_record_naming_several_heads_counts_for_each(rr):
    body = f"{HEADING}\n\nReviewed head: {OLD}\nReviewed head: {HEAD}\n"
    assert rr.decide([_comment(body)], HEAD)[0] == "success"
    assert rr.decide([_comment(body)], OLD)[0] == "success"


def test_a_comment_with_a_null_body_is_skipped(rr):
    comments = [{"id": 1, "author_association": "OWNER", "body": None}]
    assert rr.decide(comments, HEAD)[0] == "failure"


@pytest.mark.parametrize("head", ["", HEAD[:7], HEAD + "0", "g" * 40, None])
def test_a_malformed_head_is_an_error(rr, head):
    with pytest.raises(ValueError, match="head"):
        rr.decide([_comment(_record(HEAD))], head)


def test_every_description_fits_a_github_status(rr):
    cases = [[], [_comment(_record(OLD))], [_comment(_record(HEAD), comment_id=10**18)]]
    for comments in cases:
        assert len(rr.decide(comments, HEAD)[1]) <= 140


# -- parse_pages and main -----------------------------------------------------


def test_parse_pages_joins_the_pages_gh_paginate_prints(rr):
    pages = (
        json.dumps([_comment("a", comment_id=1)]) + "\n" + json.dumps([_comment("b", comment_id=2)])
    )
    assert [c["id"] for c in rr.parse_pages(pages)] == [1, 2]


def test_parse_pages_of_no_output_is_no_comment(rr):
    assert rr.parse_pages("") == []
    assert rr.parse_pages("[]\n") == []


@pytest.mark.parametrize("text", ["{}", "[1, 2]", "[{}] trailing", "not json", '[{"id": 1}] {}'])
def test_parse_pages_rejects_anything_but_arrays_of_objects(rr, text):
    with pytest.raises(ValueError, match="the comments"):
        rr.parse_pages(text)


def _run_script(args, stdin):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )


def test_main_prints_the_state_and_the_description_on_one_line():
    done = _run_script([HEAD], json.dumps([_comment(_record(HEAD), comment_id=5)]))
    assert done.returncode == 0, done.stderr
    state, text = done.stdout.rstrip("\n").split("\t")
    assert state == "success"
    assert "comment 5" in text


@pytest.mark.parametrize(
    ("args", "stdin"),
    [([], "[]"), ([HEAD[:7]], "[]"), ([HEAD], "{"), ([HEAD, "extra"], "[]")],
)
def test_main_fails_loudly_on_bad_input(args, stdin):
    done = _run_script(args, stdin)
    assert done.returncode == 2
    assert done.stdout == ""
    assert "review_record" in done.stderr


# -- the workflow -------------------------------------------------------------


def _workflow():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _steps():
    return _workflow()["jobs"]["record"]["steps"]


def test_the_workflow_has_only_the_permissions_it_needs():
    assert _workflow()["permissions"] == {
        "contents": "read",
        "statuses": "write",
        "pull-requests": "read",
    }


def test_the_workflow_checks_out_the_default_branch_only():
    checkouts = [s for s in _steps() if str(s.get("uses", "")).startswith("actions/checkout@")]
    assert len(checkouts) == 1
    step = checkouts[0]
    sha = step["uses"].split("@", 1)[1]
    assert len(sha) == 40, "pin the action by commit SHA"
    assert step["with"]["ref"] == "${{ github.event.repository.default_branch }}"
    assert step["with"]["persist-credentials"] is False
    assert step["with"]["sparse-checkout"].split() == ["scripts/review_record.py"]


def test_the_workflow_runs_the_default_branch_definition_on_every_push_and_comment():
    # PyYAML reads the bare key `on` as True. `pull_request` would run the pull
    # request's own copy of this file with `statuses: write`, so a branch that
    # edits it could post success; `pull_request_target` runs the default
    # branch's copy.
    triggers = _workflow()[True]
    assert set(triggers) == {"pull_request_target", "issue_comment"}
    assert set(triggers["pull_request_target"]["types"]) == {"opened", "synchronize", "reopened"}
    assert set(triggers["issue_comment"]["types"]) == {"created", "edited", "deleted"}
    assert "pull_request_target" in _workflow()["jobs"]["record"]["if"]


def test_the_status_step_runs_even_when_the_checkout_failed():
    (step,) = [s for s in _steps() if "run" in s]
    assert step["if"] == "${{ !cancelled() }}"


FAKE_GH = """#!/bin/sh
# A stand-in for gh: answers the three calls the workflow makes.
case "$*" in
  "api repos/o/r/pulls/7 -q .head.sha")
    [ -z "$FAKE_PULL_FAILS" ] || exit 1
    echo "$FAKE_HEAD" ;;
  "api --paginate repos/o/r/issues/7/comments")
    [ -z "$FAKE_COMMENTS_FAIL" ] || exit 1
    cat "$FAKE_COMMENTS" ;;
  "api repos/o/r/statuses/"*) printf '%s\\n' "$@" > "$FAKE_STATUS" ;;
  *) echo "fake gh: unexpected call: $*" >&2; exit 64 ;;
esac
"""


def _run_step(tmp_path, comments_text, *, expect_exit=0, head=HEAD, **extra_env):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(FAKE_GH, encoding="utf-8")
    gh.chmod(0o755)
    comments = tmp_path / "comments-fixture.json"
    comments.write_text(comments_text, encoding="utf-8")
    status = tmp_path / "status.txt"
    runner_temp = tmp_path / "runner"
    runner_temp.mkdir()
    (step,) = [s for s in _steps() if "run" in s]
    env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "REPO": "o/r",
        "PR": "7",
        "GH_TOKEN": "unused",
        "RUNNER_TEMP": str(runner_temp),
        "FAKE_HEAD": HEAD,
        "FAKE_COMMENTS": str(comments),
        "FAKE_STATUS": str(status),
        "EVENT_HEAD": "",
        **extra_env,
    }
    bash = shutil.which("bash")
    assert bash
    done = subprocess.run(
        [bash, "-e", "-c", step["run"]],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert (done.returncode == 0) == (expect_exit == 0), done.stderr
    if head is None:
        assert not status.exists()
        return None
    posted = status.read_text(encoding="utf-8").splitlines()
    assert posted[:2] == ["api", f"repos/o/r/statuses/{head}"]
    fields = dict(arg.split("=", 1) for flag, arg in itertools.pairwise(posted) if flag == "-f")
    assert fields["context"] == "review record"
    return fields


def test_the_step_posts_success_for_a_record_naming_the_head(tmp_path):
    page1 = json.dumps([_comment(_record(OLD), comment_id=1)])
    page2 = json.dumps([_comment(_record(HEAD), comment_id=2)])
    fields = _run_step(tmp_path, page1 + page2)
    assert fields["state"] == "success"
    assert "comment 2" in fields["description"]


def test_the_step_posts_failure_when_the_record_is_for_an_older_head(tmp_path):
    fields = _run_step(tmp_path, json.dumps([_comment(_record(OLD))]))
    assert fields["state"] == "failure"


def test_the_step_posts_an_error_when_the_decision_fails(tmp_path):
    fields = _run_step(tmp_path, "{not json")
    assert fields["state"] == "error"


def test_the_step_posts_an_error_when_the_comments_cannot_be_read(tmp_path):
    # A success posted earlier on this head must not survive a failed re-read.
    fields = _run_step(tmp_path, "[]", expect_exit=1, FAKE_COMMENTS_FAIL="1")
    assert fields["state"] == "error"


def test_the_step_posts_an_error_on_the_event_head_when_the_head_cannot_be_read(tmp_path):
    fields = _run_step(tmp_path, "[]", expect_exit=1, head=OLD, FAKE_PULL_FAILS="1", EVENT_HEAD=OLD)
    assert fields["state"] == "error"


def test_the_step_fails_without_a_status_when_no_head_is_known(tmp_path):
    # A comment event carries no head SHA: nothing to post on, the run fails.
    _run_step(tmp_path, "[]", expect_exit=1, head=None, FAKE_PULL_FAILS="1")
