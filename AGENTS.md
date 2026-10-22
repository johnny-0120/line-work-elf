# Contributor instructions

This is a public source distribution. Never commit local configuration, LINE
identifiers, OAuth files, chat contents, Obsidian notes, logs or databases.
Use synthetic in-memory data in tests and do not contact external services.

Keep the free-model guard: only the public internal model alias and explicit
server-configured `:free` models, with zero prompt/completion price caps.
No paid fallback. Preserve owner-only authorization, source pairing, evidence
checks and the restricted public webhook relay.

Run `python -m unittest discover -s . -p "test_*.py" -q` with an isolated
LOCALAPPDATA. Some tests import the Windows runtime module, which creates its
state directory. Run `python tools/check_public_files.py` after staging files.

Do not start services, install a supervisor, connect a personal account or run
real LINE/Calendar requests as part of automated verification.
