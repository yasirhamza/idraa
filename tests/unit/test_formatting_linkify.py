"""Unit tests for ``linkify_https`` — Sec-I1 scheme-allowlist gate (issue #349).

All hostile-scheme paths (javascript:, data:, http://) must produce inert
escaped text with NO ``<a`` emitted.  Only ``https://`` with a non-empty netloc
is allowed through.
"""

from __future__ import annotations

from markupsafe import Markup

from idraa.formatting import linkify_https


def test_linkify_https_wraps_url_with_allowlisted_anchor():
    out = linkify_https("Cyentia IRIS 2025, https://example.test/iris.pdf (accessed 2026-06-10)")
    assert isinstance(out, Markup)
    assert '<a href="https://example.test/iris.pdf"' in str(out)
    assert 'target="_blank"' in str(out) and 'rel="noopener noreferrer"' in str(out)
    assert str(out).startswith("Cyentia IRIS 2025, ")


def test_linkify_https_javascript_scheme_stays_inert():
    out = str(linkify_https("EVIL, javascript:alert(1) end"))
    assert "<a" not in out and "javascript:alert(1)" in out


def test_linkify_https_http_not_allowlisted():
    out = str(linkify_https("see http://insecure.test/x"))
    assert "<a" not in out


def test_linkify_https_escapes_surrounding_markup():
    out = str(linkify_https("<script>x</script> https://ok.test/a"))
    assert "&lt;script&gt;" in out and '<a href="https://ok.test/a"' in out


def test_linkify_https_trailing_punctuation_excluded():
    out = str(linkify_https("read https://ok.test/a.pdf."))
    assert 'href="https://ok.test/a.pdf"' in out  # trailing dot stays text


def test_linkify_https_multiple_urls():
    out = str(linkify_https("a https://x.test/1 b https://x.test/2"))
    assert out.count("<a ") == 2


def test_linkify_https_no_url_passthrough_escaped():
    out = str(linkify_https("HHS HC3 Ransomware Threat Brief TLP:WHITE (2024) & more"))
    assert "<a" not in out and "&amp;" in out


def test_linkify_https_uppercase_scheme_stays_inert():
    out = str(linkify_https("ref HTTPS://example.test/x"))
    assert "<a" not in out


def test_linkify_https_data_scheme_stays_inert():
    out = str(linkify_https("see data:text/html,<h1>x</h1>"))
    assert "<a" not in out and "&lt;h1&gt;" in out
