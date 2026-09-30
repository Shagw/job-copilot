# Security

## Reporting a vulnerability

Please **don't open a public issue**. Use GitHub's private
[security advisory form](https://github.com/Shagw/job-copilot/security/advisories/new) for this repository
(or the "Report a vulnerability" button on your fork's upstream). Include steps to reproduce. You'll get a reply
within a week.

## Running your own copy

This app stores resumes and job applications for every user who signs up, so treat a public deployment as
handling personal data.

- `ENVIRONMENT=production`, a random `JWT_SECRET` of 32+ characters and `EMAIL_MODE=smtp`. Production refuses to
  start otherwise.
- Serve it over HTTPS only (the session cookie is `Secure` in production).
- Run one process (cooldowns and rate limits are in memory) and back up `backend/storage/`.
- Your Groq (and optional TypeSafe) keys are shared by all your users. Resume text is sent to those providers;
  say so in your own privacy notice.
- Archived history is never deleted. Decide on a retention period for your users and jurisdiction.
- Keep dependencies up to date (`pip list --outdated`, `npm outdated`).
