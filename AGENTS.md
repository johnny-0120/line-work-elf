# Contributor instructions

This is a public source distribution. Never commit local configuration, LINE
identifiers, OAuth files, chat contents, Obsidian notes, logs or databases.
Use synthetic in-memory data in tests and do not contact external services.

Keep the free-model guard: only the public internal model alias and explicit
server-configured `:free` models, with zero prompt/completion price caps.
No paid fallback. Preserve owner-only Obsidian/agent authorization and isolated Calendar grants,
per-member database isolation, actor-bound source pairing, evidence checks and
the restricted public webhook relay. Public trial members get group digests
only. Never send a member's summary to the service owner or another member.

Run `python -m unittest discover -s . -p "test_*.py" -q` with an isolated
LOCALAPPDATA. Some tests import the Windows runtime module, which creates its
state directory. Run `python tools/check_public_files.py` after staging files.

Do not start services, install a supervisor, connect a personal account or run
real LINE/Calendar requests as part of automated verification.

Member Calendar is now opt-in via member_calendar.py/member_oauth.py. Each grant stays in that member SQLite calendar_tokens table. Never use owner GoogleCalendar or Obsidian context for members. Preserve signed button actor checks, browser-bound OAuth state + PKCE, one-use tickets, and the relay endpoint allowlist. No real member authorization may be performed without that person.
