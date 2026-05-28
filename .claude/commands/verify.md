# /verify — Full quality gate

Run the complete quality suite in order. Stop and report the first failure.

```bash
ruff check .
mypy probable_intel/ --ignore-missing-imports
python -m pytest tests/ -q --tb=short
pi validate nexus/apparatuses/mvp-demo.nx
pi validate nexus/apparatuses/cyber-collection.nx
```

Report: `PASS` (all green) or `FAIL: <step> — <first error>`.
