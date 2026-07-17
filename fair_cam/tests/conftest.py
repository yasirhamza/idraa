"""Pytest configuration for fair_cam's own test suite.

The idraa project's top-level pytest configuration scopes ``testpaths`` to
``tests/`` and excludes ``fair_cam`` from coverage / discovery — fair_cam tests
run against fair_cam itself. To exercise this directory directly:

    .venv/bin/python -m pytest fair_cam/tests/ --no-cov -o testpaths=fair_cam/tests

(``--no-cov`` and the ``testpaths`` override are needed because the project's
``pyproject.toml`` defines ``addopts = ["--cov=idraa", ...]`` which would
otherwise activate against this isolated suite.)
"""
