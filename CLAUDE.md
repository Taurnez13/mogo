# Taurnez — notes for AI coding agents

Taurnez is a self-hosted title-metadata and ONIX 3.0 app for small publishers. Python/FastAPI,
SQLAlchemy, SQLite, Jinja2, one CSS file, no JavaScript framework. Keep it that way.

## Rules

* Load the **antislop** skills in `.claude/skills/` before writing or changing any UI, copy or
  code (they are the anti-slop rulebook by miqdadbadjuber, MIT, vendored here). Run the delivery
  gate on your output.
* Every change to `app/onix/exporter.py` or `app/models.py` must keep `python tests/test_roundtrip.py`
  green: exports are validated against the EDItEUR ONIX 3.0 schema.
* No new runtime dependencies without a reason written in the pull request.
* Copy is sentence case, plain verbs, no marketing voice. Errors say what happened and what to do.
