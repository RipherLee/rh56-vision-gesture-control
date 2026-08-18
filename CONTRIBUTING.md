# Contributing

1. Use Python 3.10 or 3.11 and create a local virtual environment.
2. Keep hardware-dependent actions behind an explicit user opt-in.
3. Do not change calibrated gesture angles without documenting the target hand and collision checks.
4. Run `python -m unittest discover -s tests -v` before submitting changes.
5. Never commit serial logs, virtual environments, camera captures, or vendor archives.
