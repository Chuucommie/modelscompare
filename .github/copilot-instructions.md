# modelscompare Workspace Instructions

- Keep GitHub Copilot model discovery and pricing table selection dynamic; do not add static model lists or provider/model mappings.
- Show and export input, cached read, cached write, and output as separate costs; never calculate or document a combined/blended cost.
- Preserve the live AI-credit-to-USD conversion and per-million-token units.
- Keep network calls and HTML parsing separable so they can be tested with fixtures.
- Run `python -m pytest` after changes to parsing, matching, CLI filtering, or exports.

## Setup Status

- [x] Requirements specified and project scaffolded.
- [x] Interactive browser, persistent preferences, setup wizard, and live-source parsing implemented.
- [x] Python extensions verified as installed.
- [x] Full test suite, wheel build, isolated install, and packaged CLI smoke test run.
- [x] README and workspace instructions written.
- [x] No VS Code task required for this installable CLI.
- [x] Responsive terminal layout, search, help overlay, live unit toggle, JSON output, and explicit update commands implemented.
- [x] GitHub Releases update checker with SHA-256 verification, cross-platform self-update, and user-data preservation implemented.
- [x] Multipiattaforma build script and CI matrix for Windows, macOS, and Linux implemented.
- [ ] Live interactive launch deferred; run `modelscompare` in a network-connected terminal to try it.
