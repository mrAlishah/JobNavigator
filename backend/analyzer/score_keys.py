"""Job scores are stored by résumé id, shown by résumé name (3.1).

The scoring prompt lists the résumés by *label* (the résumé's name) and the model answers
`{"scores": {"PM – Platform": 82, ...}, "best_cv": "PM – Platform"}`. Before 3.1 that answer
was stored as it came back, so `jobs.cv_scores`, `scoring_report` and `best_cv` were keyed by
name and a renamed or duplicate-named résumé mixed or lost its scores. Now:

- The scorer's text sets are `ScoringSet`s: a plain `{label: text}` dict (what the prompt and
  every older caller read) that also knows each label's storage key — the résumé id for a
  résumé, "Persona" for the Persona, "Tailored" for a job's tailored copy.
- `keyed()` turns the model's label-keyed answer into key-keyed scores before they are stored;
  `job.cv_score_names` keeps {résumé id: name at scoring time} for résumés deleted later.
- `display()` turns the stored keys back into names for the API and Telegram: the résumé's
  *current* name, else its name when scored, else "Deleted résumé". A key that is not a
  résumé id (a 3.0 name key no migration could map, "Tailored", "Persona") is shown as it is.
"""
import uuid
from typing import Optional

TAILORED = "Tailored"
PERSONA = "Persona"
DELETED = "Deleted résumé"


def is_resume_key(key) -> bool:
    """A stored key that is a résumé id (not a name, "Tailored" or "Persona")."""
    try:
        uuid.UUID(str(key))
        return True
    except (ValueError, TypeError, AttributeError):
        return False


class ScoringSet(dict):
    """{label: text} for one scoring prompt, plus each label's storage key (`key_of`)."""

    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self.key_of: dict = {}

    def add(self, label: str, text: str, key: str) -> str:
        """Add an entry under a label unique in this set ("Name", "Name (2)", …); returns it.
        Two résumés may share a name, but the model can only answer one score per label."""
        unique, n = label, 2
        while unique in self:
            unique, n = f"{label} ({n})", n + 1
        self[unique] = text
        self.key_of[unique] = key
        return unique

    def update(self, other=(), **kw):   # noqa: D102 - dict.update that keeps the keys too
        for label, text in dict(other, **kw).items():
            key = getattr(other, "key_of", {}).get(label, label)
            if label in self and self.key_of.get(label, label) == key:   # the same entry again
                super().__setitem__(label, text)
                self.key_of[label] = key
            else:
                self.add(label, text, key)


def key_for(label, cv_texts) -> str:
    """The storage key of a label the model answered with (the label itself when unknown:
    a plain dict from an older caller, or a label the model made up)."""
    return getattr(cv_texts, "key_of", {}).get(label, label)


def keyed(scores: dict, cv_texts) -> dict:
    """The model's {label: score} as {storage key: score}."""
    return {key_for(label, cv_texts): v for label, v in (scores or {}).items()}


def names_for(cv_texts, labels) -> dict:
    """{résumé id: label} for the labels answered, to keep a deleted résumé's name."""
    out = {}
    for label in labels or ():
        key = key_for(label, cv_texts)
        if is_resume_key(key):
            out[key] = _base_label(label, cv_texts)
    return out


def _base_label(label: str, cv_texts) -> str:
    """The résumé's own name: a label made unique ("Name (2)") is stored without the suffix."""
    import re
    m = re.match(r"^(.*) \((\d+)\)$", label)
    if m and m.group(1) in cv_texts:
        return m.group(1)
    return label


def resume_names(db) -> dict:
    """{résumé id: current name} for the current account (the query is account-scoped)."""
    from backend.models.db import Resume
    return {str(rid): name for rid, name in db.query(Resume.id, Resume.name).all()}


def display(cv_scores, scoring_report, best_cv, snapshot, names: dict):
    """(scores, report, best) with résumé ids replaced by names; two résumés that show the
    same name get "Name (2)" so neither score is lost."""
    snapshot = snapshot or {}
    label_of: dict = {}
    used: set = set()

    def label(key):
        if key in label_of:
            return label_of[key]
        if not is_resume_key(key):
            name = key
        else:
            name = names.get(str(key)) or snapshot.get(str(key)) or DELETED
        unique, n = name, 2
        while unique in used:
            unique, n = f"{name} ({n})", n + 1
        used.add(unique)
        label_of[key] = unique
        return unique

    # Non-résumé keys ("Tailored", "Persona", legacy names, "_skipped") keep their own text first.
    for key in (cv_scores or {}):
        if not is_resume_key(key):
            label(key)
    scores = None if cv_scores is None else {label(k): v for k, v in cv_scores.items()}
    report = scoring_report
    if isinstance(scoring_report, dict) and "summary" not in scoring_report:
        report = {label(k): v for k, v in scoring_report.items()}
    best = label(best_cv) if best_cv else best_cv
    return scores, report, best


def display_best(job, names: Optional[dict] = None, db=None) -> Optional[str]:
    """The best résumé's display name for one job (Telegram, application history)."""
    if not getattr(job, "best_cv", None):
        return getattr(job, "best_cv", None)
    if names is None:
        names = resume_names(db) if db is not None else {}
    return display(getattr(job, "cv_scores", None), None, job.best_cv,
                   getattr(job, "cv_score_names", None), names)[2]
