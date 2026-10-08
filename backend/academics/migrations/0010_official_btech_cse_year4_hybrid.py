from django.db import migrations


OFFLINE_DAYS = {
    'CS-4A': 0,
    'CS-4B': 1,
    'CS-4C': 2,
    'CS-4D': 3,
    'CS-4E': 4,
    'CS-4F': 0,
    'CS-4G': 1,
    'CS-4H': 2,
    'CS-4I': 4,
    'CS-4J': 0,
    'CS-4K': 4,
    'CS-4L': 1,
    'CSE(AI)-4A': 3,
    'CSE(AI)-4B': 0,
    'CSE(AI)-4C': 1,
    'CSE(AI)-4D': 1,
    'CSE(AI)-4E': 4,
    'CSE(AI)-4F': 0,
    'CSE(CCML)-4A': 3,
    'CSE(IOTBC)-4A': 1,
}


def add_official_policies(apps, schema_editor):
    Section = apps.get_model('academics', 'Section')
    Policy = apps.get_model('academics', 'SectionDeliveryPolicy')
    for section_name, weekday in OFFLINE_DAYS.items():
        matches = Section.objects.filter(
            name=section_name,
            program__code='BTECH-CSE',
            year=4,
            semester__session__name='2026-27',
            semester__type='ODD',
        )
        match_count = matches.count()
        if match_count == 0:
            # Test databases and deployments that apply schema before importing
            # academic master data can safely run this migration; the data seed
            # is intentionally limited to rows that already exist.
            continue
        if match_count != 1:
            raise RuntimeError(
                f'Expected exactly one 2026-27 Odd BTECH-CSE Year-4 section '
                f'{section_name}; found {match_count}.'
            )
        section = matches.get()
        Policy.objects.update_or_create(
            section=section,
            academic_session_id=section.semester.session_id,
            semester_id=section.semester_id,
            defaults={
                'mode': 'HYBRID',
                'offline_weekday': weekday,
                'source': 'OFFICIAL_TIMETABLE',
                'active': True,
            },
        )


def remove_official_policies(apps, schema_editor):
    Policy = apps.get_model('academics', 'SectionDeliveryPolicy')
    Policy.objects.filter(
        source='OFFICIAL_TIMETABLE',
        academic_session__name='2026-27',
        semester__type='ODD',
        section__program__code='BTECH-CSE',
        section__year=4,
        section__name__in=OFFLINE_DAYS,
    ).delete()


class Migration(migrations.Migration):
    dependencies = [('academics', '0009_sectiondeliverypolicy')]

    operations = [migrations.RunPython(add_official_policies, remove_official_policies)]
