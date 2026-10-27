# Recorded test samples

Real public responses recorded once (October 2026) so the tests run against the actual formats
of job boards, list pages and careers pages — without touching the network.

**They are trimmed and scrubbed copies.** Before committing, each sample was cut down to what
its test reads and personal data was removed: people's names (founders, partners, team,
recruiters) are replaced by `[name]`, personal email addresses by `contact@example.com`,
personal LinkedIn links, team / testimonial blocks and photos are removed, and image
addresses are replaced by a placeholder. Company data (names, websites, descriptions, jobs)
is kept. The full originals are kept locally only, in `local/fixtures-original/`
(gitignored).

When you add a sample: record it, keep the original under `local/`, then trim and scrub the
committed copy the same way and check it (`git grep` for names, emails, `linkedin.com/in/`).
