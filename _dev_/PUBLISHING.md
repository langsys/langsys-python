# Publishing Guide for langsys

`langsys` publishes to [PyPI](https://pypi.org/project/langsys/) via GitHub Actions **trusted
publishing** (OIDC) — no long-lived PyPI token is stored anywhere.

## How a release happens

1. Bump `version` in `pyproject.toml` and update `CHANGELOG.md`.
2. Commit and push to `main`, then tag the release:
   ```bash
   git tag -a vX.Y.Z -m "Release vX.Y.Z"
   git push origin main vX.Y.Z
   ```
3. Create a GitHub Release for the tag:
   ```bash
   gh release create vX.Y.Z --generate-notes
   ```
4. The `release: published` event triggers `.github/workflows/publish.yml`, which runs
   `ruff check` → `mypy src` → `pytest -m "not integration"` → `python -m build` →
   `pypa/gh-action-pypi-publish`, inside the `pypi` GitHub Environment using OIDC.

## Trust handshake (must stay in sync)

- GitHub Environment name: `pypi`
- PyPI trusted-publisher config: this repository + workflow filename `publish.yml` + environment `pypi`
- `.github/workflows/publish.yml`: `environment: pypi`

## First publish

A brand-new project name needs its PyPI trusted publisher configured before the first release
— PyPI supports pre-registering a "pending" trusted publisher for a name that doesn't exist
yet. After that, every GitHub Release publishes automatically from CI.

## CI gate

`.github/workflows/ci.yml` runs the lint, type, and test checks on every push and pull request,
independent of the release flow.

## Manual publishing (fallback)

```bash
python -m build
twine upload dist/*        # requires a PyPI token with upload access
```
