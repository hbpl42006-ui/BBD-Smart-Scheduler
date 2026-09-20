You are a senior full-stack architect and engineer. Build a production-ready academic Timetable & Room Allocation Management System using Django + Django REST Framework for the backend and Next.js App Router + TypeScript for the frontend.

PROJECT GOAL
Create a web application for a university/college that can manage academic master data, generate section-wise weekly timetables, allocate classrooms/labs, prevent faculty/room/section clashes, maintain timetable versions, publish official revisions, notify faculty whenever an official timetable changes, and generate downloadable PDFs that faculty can share with student groups.

REFERENCE FORMAT TO SUPPORT
The existing official timetable format is a weekly grid with Monday-Friday rows and hourly columns (for example 09-10, 10-11, 11-12, 12-1, lunch/break, 2-3, 3-4, 4-5). A cell may display compact metadata such as class type/course short code/faculty initials/room number. Below the grid, the official sheet contains course credit, course code, course name, metadata, faculty name, class coordinator, academic session, section/year and approval/signature area. The generated PDF must be able to reproduce this type of official layout without storing the cell as one raw string.

TECH STACK
Backend: Python, Django, Django REST Framework, PostgreSQL, SimpleJWT or secure cookie-based JWT, Celery, Redis, django-filter. Use OR-Tools CP-SAT for automatic generation. Use WeasyPrint for official HTML-to-PDF generation. Django Channels is optional for live update events.
Frontend: Next.js App Router, TypeScript, Tailwind CSS, shadcn/ui, TanStack Query, React Hook Form, Zod. Build responsive desktop/mobile UI.

ARCHITECTURE RULES
1. Use a modular Django app structure: accounts, institutions, academics, faculty, rooms, scheduling, approvals, notifications, reports, audit, common.
2. Keep business logic in service modules, not fat views/serializers.
3. Published timetable versions are immutable. Every modification after publication creates a new draft revision.
4. The section timetable, faculty timetable and room allocation chart must all be projections of the same ScheduleEntry data. Never maintain a separate room-allotment source of truth.
5. Use database transactions for version publication and concurrency-sensitive updates.
6. Add audit records for master-data changes, schedule changes, approval and publication.
7. Use soft deletion/protection for master data referenced by historical versions.

ROLES
SUPER_ADMIN
ACADEMIC_ADMIN / TIMETABLE_COORDINATOR
HOD_OR_DEAN_APPROVER
CLASS_COORDINATOR
FACULTY
ROOM_LAB_COORDINATOR
READ_ONLY_VIEWER
Enforce permissions in DRF and mirror them in the UI.

CORE MODELS
Institution(name, code, logo, timezone)
Department(institution, name, code)
Program(department, name, code, duration_years)
AcademicSession(institution, name, start_date, end_date, is_active)
Semester(session, name/number, type ODD|EVEN, start_date, end_date)
Section(program, semester or current academic context, year, name, student_strength, coordinator)
Faculty(user, employee_code, initials, department, max_daily_periods, max_weekly_periods)
FacultyAvailability(faculty, weekday, time_slot, is_available, preference_weight)
Course(code, name, credit, short_code)
CourseOffering(semester, section, course, weekly_periods, default_class_type, required_block_size, room_type_requirement, preferred_room, active)
CourseOfferingFaculty(course_offering, faculty, role, priority)
Room(code, building, floor, capacity, room_type, facilities JSON/relations, active)
RoomAvailability(room, weekday/date, time_slot, status AVAILABLE|BLOCKED|MAINTENANCE, reason)
TimeSlotTemplate(name, institution)
TimeSlot(template, label, start_time, end_time, order, is_break)
Timetable(session, semester, department/scope, title, effective_date)
TimetableVersion(timetable, version_no, status DRAFT|PENDING_APPROVAL|APPROVED|PUBLISHED|SUPERSEDED, previous_version, created_by, published_by, published_at, notes)
ScheduleEntry(version, section, course_offering, weekday, start_slot, block_length, room, entry_type LECTURE|TUTORIAL|PRACTICAL|COMMON|LIBRARY|OTHER, locked, note)
ScheduleEntryFaculty(schedule_entry, faculty, role)
Approval(version, approver, status, comment, acted_at)
ChangeLog(from_version, to_version, object_type, object_id, change_type, old_data JSON, new_data JSON)
Notification(recipient, channel, title, body, payload JSON, delivery_status, read_at, acknowledged_at)
GeneratedArtifact(version, artifact_type, scope_id, file, checksum, generated_at)
ShareLink(timetable/version pointer, section optional, token, active, expires_at, always_latest)

HARD VALIDATION CONSTRAINTS
- No section double booking.
- No faculty double booking across all sections/departments.
- No room double booking.
- Faculty availability must be respected.
- Room/lab must be active and available.
- Room capacity must satisfy section strength unless an authorised override is stored.
- Room type/facilities must satisfy course/lab requirements.
- Lunch/common locked slots cannot be overwritten.
- Practical/lab block_length must remain consecutive.
- A published version cannot be patched/deleted.
- Before publish, required weekly periods for every course offering must be complete or explicitly waived by an approver.

SOFT OPTIMISATION CONSTRAINTS
- Minimise faculty and section gaps.
- Avoid excessive consecutive lectures.
- Spread the same course across the week.
- Prefer same classroom for a section when possible.
- Respect faculty preferred slots.
- Balance room utilisation.
- Prefer appropriate-sized rooms.
- Support configurable weights for each preference.

AUTOMATIC GENERATION
Implement OR-Tools CP-SAT generation as a Celery task.
- Input: selected timetable version/scope, sections, course offerings, faculty availability, rooms, time slots and locked ScheduleEntry records.
- Locked entries become fixed constraints.
- Generate a valid solution satisfying hard constraints and optimising soft constraints.
- Persist solution only to DRAFT version.
- Return job status/progress plus solution score/warnings.
- Allow a configurable solver time limit.
- Include “regenerate only affected sections” or partial generation later; structure code so this is possible.

MANUAL BUILDER
Create /timetables/[id]/builder.
- Weekly grid with weekdays as rows and time slots as columns.
- Filter by session, semester, department, program, year, section and version.
- Click a cell to add/edit an entry; select course offering, class type, faculty, room and block length.
- Drag/move is optional initially but architecture should allow it.
- Show course short code + faculty initials + room in each cell.
- Conflict badges update after edits.
- Allow lock/unlock entry.
- Inspector panel shows periods required/scheduled/remaining, faculty availability and recommended free rooms.
- Save draft, validate, compare, submit for approval, publish, download PDF.
- Published version is read-only.

ROOM ALLOCATION
Build /room-allocation.
- Day/week filters.
- Room x period matrix or period x room matrix.
- Show occupied, free and blocked/maintenance states.
- Occupied cell shows section + course + faculty.
- Filter by building, floor, room type, capacity and department.
- “Find free room” endpoint/action based on day, slot/block, capacity and room type.
- Room chart must be calculated from ScheduleEntry; it must automatically change when the timetable changes.
- Export official Room Allotment Chart PDF.

VERSIONING AND PUBLICATION WORKFLOW
DRAFT -> VALIDATE -> PENDING_APPROVAL (optional) -> APPROVED -> PUBLISHED.
When changing a published timetable, clone it into a new DRAFT version. Never edit PUBLISHED in place.
On publish:
1. Acquire a lock and run final validation.
2. Verify approval requirement.
3. Calculate structured diff against previous PUBLISHED version.
4. In one transaction mark new version PUBLISHED and previous PUBLISHED version SUPERSEDED.
5. After transaction commit, queue PDF generation and notification jobs.
6. Update any “latest published” share pointer.
7. Write audit log.

CHANGE DIFF
Return changes grouped by section/faculty/day.
Represent ADDED, REMOVED, MOVED and MODIFIED entries.
A diff item should include old/new day, slot, room, course and faculty where relevant.
Create a clear UI for “Previous vs New”.

NOTIFICATIONS
On every official publication:
- ALL active faculty receive a “New timetable version published” notification with effective date and download/latest link.
- Faculty whose timetable changed receive a detailed personalised diff.
- Class coordinators receive detailed changes for their sections and section PDF/share link.
- Approvers receive publication status summary.
Channels:
1. In-app notification centre.
2. Email (MVP).
3. Optional WhatsApp Cloud API adapter.
4. Optional web push/PWA adapter.
Use Celery retries and store delivery status.
Add faculty “Acknowledge update” action.

PDF/EXPORT
Use server-side HTML/CSS templates + WeasyPrint.
Exports:
- Section timetable PDF matching the reference structure.
- Faculty personal timetable PDF.
- Room allocation PDF.
- Combined department/batch timetable PDF.
Every official PDF must include timetable version, effective date and generated timestamp.
Allow institution logo and authorised signature images, but keep public exports free of unnecessary private contact information.

DISPLAY CODE
Do not store strings like L/CAIT/SY/411 as the schedule source. Build display strings from structured data, e.g.
{entry_type_short}/{course.short_code}/{faculty.initials}/{room.code}
Make display-format rules configurable enough for lectures/practicals/common activities.

API ENDPOINTS
Implement REST endpoints for:
/auth/login, refresh, me
/academic/sessions, semesters, programs, sections
/courses, course-offerings
/faculty and faculty/{id}/availability
/rooms and /rooms/available
/time-slot-templates and time-slots
/timetables
/timetables/{id}/versions
/versions/{id}/entries
/versions/{id}/validate
/versions/{id}/generate
/generation-jobs/{id}
/versions/{id}/diff
/versions/{id}/submit
/versions/{id}/approve or reject
/versions/{id}/publish
/versions/{id}/exports/...
/me/timetable
/notifications and /notifications/{id}/acknowledge
/audit
Use serializers with validation but move cross-model scheduling rules to service classes.

NEXT.JS PAGES
/login
/dashboard
/master/academic
/master/courses
/master/faculty
/master/rooms
/master/time-slots
/timetables
/timetables/[id]/builder
/timetables/[id]/generate
/timetables/[id]/validate
/timetables/[id]/versions
/room-allocation
/faculty/me/timetable
/notifications
/reports
/share/[token]

BULK DATA SETUP
Add CSV/XLSX import/export for faculty, courses/course offerings, sections and rooms. Return row-level validation errors. Imports must be transactional or support dry-run preview before commit.

DASHBOARD
Show:
- Current published version/effective date.
- Draft versions.
- Blocking conflicts.
- Pending approvals.
- Faculty update acknowledgements.
- Room utilisation.
- Latest timetable changes.

TESTING
Backend: pytest or Django TestCase. Include tests for faculty clash, room clash, section clash, capacity/type checks, locked entries, version immutability, publish transaction, diff calculation, notification recipients and permissions.
Frontend: unit/component tests for builder state, conflict display and permission-gated actions; add Playwright E2E for create draft -> edit -> validate -> approve -> publish -> faculty notification/download.

SEED/DEMO DATA
Provide a management command to seed one institution, one CSE department, multiple sections, 8 daily slots with a lunch break, faculty, courses and rooms/labs. Include enough sample data to demonstrate conflicts, room chart and timetable generation without copying personal phone numbers from the reference PDFs.

IMPLEMENTATION ORDER
Milestone 1: project setup, auth/RBAC, master data and imports.
Milestone 2: timetable/version/schedule models, validation engine and read-only grids.
Milestone 3: editable builder, room allocation and free-room finder.
Milestone 4: approval, publish, diff, audit and official PDF exports.
Milestone 5: in-app/email notifications and faculty acknowledgement.
Milestone 6: OR-Tools automatic generation and optimisation.
Milestone 7: reports, WhatsApp/push adapters and UX polish.

CODING QUALITY
- Use type hints and service classes in Django.
- Use DRF viewsets/actions only where appropriate; keep endpoints explicit for domain actions.
- Use select_related/prefetch_related and indexes.
- Use atomic transactions for multi-step writes.
- Use idempotent Celery tasks for PDFs/notifications.
- Use environment variables for secrets.
- Provide Docker Compose for local PostgreSQL + Redis.
- Add .env.example, README setup steps, migrations and seed command.
- Keep UI accessible and responsive.
- Do not mock core scheduling logic. Implement real conflict validation and a real room availability query.

DELIVERABLE EXPECTATION
First inspect the existing repository before changing code. Then present the intended file/module changes and implement milestone by milestone. Do not rewrite unrelated parts. After each milestone run backend tests, frontend typecheck/lint/build and explain any remaining issue. Keep migrations clean and include API examples for the major scheduling workflows.