# Milestone 6 reports and production notes

## Reports

Authenticated users can use the following data-derived endpoints. Reports use
the latest published version by default; management users can select a version
with `version=<uuid>`.

- `/api/reports/faculty-workload/`
- `/api/reports/room-utilization/`
- `/api/reports/section-timetable/`
- `/api/reports/faculty-timetable/`
- `/api/reports/room-timetable/`
- `/api/reports/course-allocation/`
- `/api/reports/unscheduled/`
- `/api/reports/conflicts/`
- `/api/reports/free-rooms/`
- `/api/reports/version-activity/`
- `/api/reports/analytics/`

List reports support `export=csv` and `export=xlsx` where applicable. CSV is
UTF-8 encoded. XLSX output contains a header row and readable scalar values.
Faculty users are restricted to their own Faculty data and published versions.
Management roles retain cross-Faculty reporting access.

## Production configuration

Copy `backend/.env.example` to a private `backend/.env` and set real values.
Never commit `.env`, database files, SMTP passwords, JWT secrets, or WhatsApp
credentials. `DATABASE_URL` may use PostgreSQL; when it is absent, local SQLite
remains the development default. Configure `CSRF_TRUSTED_ORIGINS`,
`ALLOWED_HOSTS`, and HTTPS flags for a deployed environment.

The health endpoint is `/api/health/` and returns only `{ "status": "ok" }`.
Email uses Django's configured backend. WhatsApp requires a Meta access token,
phone-number ID, app secret, verify token, approved templates, and a valid
public webhook URL.
