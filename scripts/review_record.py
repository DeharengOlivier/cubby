"""Decide the required ``review record`` status of a pull request's head commit.

A review record is a pull request comment that:

- starts with ``## Independent review record`` (a suffix such as
  ``(re-review)`` may follow on the same line);
- was posted by the owner, a member or a collaborator (the repository is
  public and anyone may comment);
- has a line ``Reviewed head: <sha>`` naming the commit the reviewer read,
  with the full 40-digit SHA (optionally in backticks, either case, nothing
  else on the line but spaces). A short SHA is refused: a prefix could match
  a later commit.

The status is ``success`` only when a record names the pull request's current
head. A push after the review therefore turns it back to ``failure`` until a
new record names the new head.

    gh api --paginate repos/OWNER/REPO/issues/N/comments > comments.json
    python3 scripts/review_record.py HEAD_SHA < comments.json

It prints ``state<TAB>description`` and exits 0, or exits 2 on malformed
input. It uses only the standard library: the workflow runs it with the
runner's own ``python3``, without installing the project.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

HEADING = "## Independent review record"
TRUSTED = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
_SHA = re.compile(r"[0-9a-fA-F]{40}")
_REVIEWED_HEAD = re.compile(r"Reviewed head: (?:`([0-9a-fA-F]{40})`|([0-9a-fA-F]{40}))")
# GitHub refuses a status description longer than this.
MAX_DESCRIPTION = 140


def reviewed_heads(body: str) -> set[str]:
    """The SHAs, in lowercase, named by the body's ``Reviewed head:`` lines."""
    heads = set()
    for line in body.splitlines():
        match = _REVIEWED_HEAD.fullmatch(line.strip())
        if match:
            heads.add((match.group(1) or match.group(2)).lower())
    return heads


def _is_record(comment: Mapping[str, Any]) -> bool:
    body = comment.get("body")
    return (
        comment.get("author_association") in TRUSTED
        and isinstance(body, str)
        and body.startswith(HEADING)
    )


def decide(comments: Iterable[Mapping[str, Any]], head_sha: str) -> tuple[str, str]:
    """Return the status state and description for ``head_sha``."""
    if not isinstance(head_sha, str) or not _SHA.fullmatch(head_sha):
        raise ValueError(f"the head must be a full 40-digit SHA, got {head_sha!r}")
    head = head_sha.lower()
    records = [c for c in comments if _is_record(c)]
    for record in records:
        if head in reviewed_heads(record["body"]):
            description = f"record for {head[:7]} (comment {record.get('id')})"
            return "success", description[:MAX_DESCRIPTION]
    if not records:
        return "failure", "post the independent review record, with 'Reviewed head: <sha>'"
    return "failure", f"no record names the head {head[:12]}: post a re-review naming it"


def parse_pages(text: str) -> list[dict[str, Any]]:
    """Join the JSON arrays ``gh api --paginate`` prints, one per page."""
    decoder = json.JSONDecoder()
    comments: list[dict[str, Any]] = []
    index = 0
    while True:
        while index < len(text) and text[index].isspace():
            index += 1
        if index == len(text):
            return comments
        try:
            page, index = decoder.raw_decode(text, index)
        except json.JSONDecodeError as error:
            raise ValueError(f"the comments are not JSON: {error}") from error
        if not isinstance(page, list) or not all(isinstance(c, dict) for c in page):
            raise ValueError("the comments must be JSON arrays of objects")
        comments.extend(page)


def main(argv: Sequence[str], stdin: str) -> int:
    if len(argv) != 1:
        print("review_record: usage: review_record.py HEAD_SHA < comments.json", file=sys.stderr)
        return 2
    try:
        state, description = decide(parse_pages(stdin), argv[0])
    except ValueError as error:
        print(f"review_record: {error}", file=sys.stderr)
        return 2
    print(f"{state}\t{description}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], sys.stdin.read()))
