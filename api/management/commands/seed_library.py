import json
import re
import unicodedata
from pathlib import Path

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from api.models import books, borrow

CATALOG_PATH = Path(__file__).resolve().parents[2] / 'seed_data' / 'catalog.json'
FIRST_CATALOG_NUMBER = 1001

# Surnames that span more than one word, so authors sort by the right name.
COMPOUND_SURNAMES = {
    'Gabriel García Márquez': ('García Márquez', 'Gabriel'),
    'Daphne du Maurier': ('du Maurier', 'Daphne'),
    'Ursula K. Le Guin': ('Le Guin', 'Ursula K.'),
}


def ascii_slug(text):
    folded = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('ascii')
    return re.sub(r'[^a-z0-9]+', '_', folded.lower()).strip('_')


def author_username(full_name):
    """'Jane Austen' -> 'austen_jane', so the API's username ordering is surname order."""
    if full_name in COMPOUND_SURNAMES:
        surname, given = COMPOUND_SURNAMES[full_name]
    else:
        *given_parts, surname = full_name.split()
        given = ' '.join(given_parts)
    return ascii_slug(f'{surname} {given}')


def copies_for(index):
    """Deterministic stock: mostly 2 to 4 copies, with an occasional single-copy title."""
    return 1 if index % 7 == 0 else 2 + (index % 3)


class Command(BaseCommand):
    help = 'Load the real-book catalog (api/seed_data/catalog.json) into the library.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--reset',
            action='store_true',
            help='Delete every existing book, loan, and author account first.',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if not CATALOG_PATH.exists():
            raise CommandError(f'Missing catalog file: {CATALOG_PATH}')
        catalog = json.loads(CATALOG_PATH.read_text(encoding='utf-8'))

        if options['reset']:
            author_ids = list(
                User.objects.filter(books__isnull=False, is_staff=False).values_list('id', flat=True).distinct()
            )
            loans = borrow.objects.all().delete()[0]
            removed_books = books.objects.all().delete()[0]
            removed_authors = User.objects.filter(id__in=author_ids).delete()[0]
            self.stdout.write(
                f'Reset: removed {removed_books} books, {loans} loans, and the old author accounts '
                f'({removed_authors} rows including their books).'
            )
        elif books.objects.exists():
            raise CommandError('The catalog is not empty. Pass --reset to replace it.')

        authors = {}
        for entry in catalog:
            name = entry['author']
            if name in authors:
                continue
            username = author_username(name)
            user = User(username=username, email=f'{username}@authors.invalid', is_active=True)
            user.set_unusable_password()
            authors[name] = user
        User.objects.bulk_create(authors.values())
        by_name = {u.username: u for u in User.objects.filter(username__in=[a.username for a in authors.values()])}

        to_create = []
        for index, entry in enumerate(catalog):
            owner = by_name[author_username(entry['author'])]
            to_create.append(books(
                user=owner,
                title=entry['title'],
                name=entry['author'],
                genre=entry['genre'],
                description=entry['description'],
                num=FIRST_CATALOG_NUMBER + index,
                cover_url=entry.get('cover_url'),
                isbn=entry.get('isbn'),
                publish_year=entry.get('publish_year'),
                pages=entry.get('pages'),
                copies=copies_for(index + 1),
            ))
        books.objects.bulk_create(to_create)

        self.stdout.write(self.style.SUCCESS(
            f'Loaded {len(to_create)} books by {len(authors)} authors '
            f'(catalog numbers {FIRST_CATALOG_NUMBER} to {FIRST_CATALOG_NUMBER + len(to_create) - 1}).'
        ))
