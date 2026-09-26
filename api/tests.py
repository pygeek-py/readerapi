import base64
import json
import re
from datetime import timedelta
from unittest import mock

from django.contrib.auth.models import User
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.core.cache import cache
from django.core.mail.backends.base import BaseEmailBackend
from django.test import SimpleTestCase, override_settings
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework.test import APITestCase
from rest_framework import status
from rest_framework.authtoken.models import Token

from . import gmail_backend
from .models import books, borrow
from .tokens import email_verification_token
from .views import BOOKS_PAGE_SIZE


def due_in(days):
    return (timezone.localdate() + timedelta(days=days)).isoformat()


@override_settings(REQUIRE_EMAIL_VERIFICATION=False)
class SignupTests(APITestCase):
    def test_valid_signup_creates_user_with_hashed_password(self):
        response = self.client.post('/signup/', {
            'username': 'alice',
            'email': 'alice@example.com',
            'password': 'strongpass123',
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        user = User.objects.get(username='alice')
        self.assertNotEqual(user.password, 'strongpass123')
        self.assertTrue(user.check_password('strongpass123'))
        self.assertNotIn('password', response.data)
        self.assertTrue(user.is_active, 'accounts are active immediately; there is no email verification step')
        self.assertIn('token', response.data)
        self.assertTrue(Token.objects.filter(user=user, key=response.data['token']).exists())

    def test_duplicate_email_rejected(self):
        User.objects.create_user(username='someone', email='taken@example.com', password='pass12345')
        response = self.client.post('/signup/', {
            'username': 'newperson',
            'email': 'taken@example.com',
            'password': 'pass12345',
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('email', response.data)

    def test_duplicate_username_rejected(self):
        User.objects.create_user(username='bob', password='pass12345')
        response = self.client.post('/signup/', {
            'username': 'bob',
            'email': 'bob2@example.com',
            'password': 'pass12345',
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('username', response.data)

    def test_missing_fields_rejected(self):
        response = self.client.post('/signup/', {'username': 'carol'})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class SigninTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='dave', password='pass12345')

    def test_valid_login_returns_token(self):
        response = self.client.post('/signin/', {'username': 'dave', 'password': 'pass12345'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('token', response.data)
        self.assertEqual(response.data['username'], 'dave')
        self.assertTrue(Token.objects.filter(user=self.user, key=response.data['token']).exists())

    def test_invalid_password_rejected(self):
        response = self.client.post('/signin/', {'username': 'dave', 'password': 'wrong'})
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_unknown_username_rejected(self):
        response = self.client.post('/signin/', {'username': 'nobody', 'password': 'wrong'})
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_missing_credentials_rejected(self):
        response = self.client.post('/signin/', {'username': 'dave'})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class PasswordResetTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='henry', email='henry@example.com', password='oldpass123')

    def test_request_sends_email_for_known_address(self):
        response = self.client.post('/password-reset/', {'email': 'henry@example.com'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(mail.outbox), 1)

    def test_request_is_silent_for_unknown_address(self):
        response = self.client.post('/password-reset/', {'email': 'ghost@example.com'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(mail.outbox), 0)

    def test_confirm_with_valid_token_changes_password(self):
        uidb64 = urlsafe_base64_encode(force_bytes(self.user.pk))
        reset_token = default_token_generator.make_token(self.user)
        response = self.client.post('/password-reset-confirm/', {
            'uid': uidb64, 'token': reset_token, 'password': 'brandnewpass123',
        })
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('brandnewpass123'))

    def test_confirm_with_invalid_token_rejected(self):
        uidb64 = urlsafe_base64_encode(force_bytes(self.user.pk))
        response = self.client.post('/password-reset-confirm/', {
            'uid': uidb64, 'token': 'bogus-token', 'password': 'brandnewpass123',
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('oldpass123'))

    def test_confirm_with_short_password_rejected(self):
        uidb64 = urlsafe_base64_encode(force_bytes(self.user.pk))
        reset_token = default_token_generator.make_token(self.user)
        response = self.client.post('/password-reset-confirm/', {
            'uid': uidb64, 'token': reset_token, 'password': 'short',
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class LogoutTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='erin', password='pass12345')
        self.token = Token.objects.create(user=self.user)

    def test_logout_revokes_token(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {self.token.key}')
        response = self.client.post('/logout/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(Token.objects.filter(user=self.user).exists())

    def test_logout_requires_authentication(self):
        response = self.client.post('/logout/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class BorrowAuthorizationTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='frank', password='pass12345')
        self.token = Token.objects.create(user=self.owner)
        self.book = books.objects.create(
            user=self.owner, title='Dune', description='Desert planet',
            genre='Fiction', name='Frank Herbert', num=1, copies=3,
        )

    def test_borrow_requires_authentication(self):
        response = self.client.post('/borrow/', {'num': 1, 'imprint': 'First', 'due': due_in(7)})
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_book_detail_exposes_author_id_without_pii(self):
        response = self.client.get('/each/1/')
        self.assertEqual(response.data['author_id'], self.owner.id)
        self.assertNotIn('user', response.data)

    def test_borrow_ignores_client_supplied_user_and_uses_request_user(self):
        other_user = User.objects.create_user(username='mallory', password='pass12345')
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {self.token.key}')
        response = self.client.post('/borrow/', {
            'user': other_user.id,  # attempt to impersonate another user
            'num': 1, 'imprint': 'First', 'due': due_in(7),
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        created = borrow.objects.get(id=response.data['id'])
        self.assertEqual(created.user, self.owner)

    def test_borrow_copies_title_and_author_from_the_catalog_not_the_client(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {self.token.key}')
        response = self.client.post('/borrow/', {
            'title': 'Forged Title', 'name': 'Forged Author',
            'num': 1, 'imprint': 'First', 'due': due_in(7),
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        created = borrow.objects.get(id=response.data['id'])
        self.assertEqual((created.title, created.name), ('Dune', 'Frank Herbert'))

    def test_user_cannot_view_another_users_borrowed_books(self):
        other_user = User.objects.create_user(username='mallory', password='pass12345')
        other_token = Token.objects.create(user=other_user)
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {other_token.key}')
        response = self.client.get(f'/userbo/{self.owner.id}/')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class BorrowRulesTests(APITestCase):
    def setUp(self):
        librarian = User.objects.create_user(username='librarian', password='pass12345')
        self.member = User.objects.create_user(username='iris', password='pass12345')
        self.token = Token.objects.create(user=self.member)
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {self.token.key}')
        self.single = books.objects.create(
            user=librarian, title='Rare Book', description='d', genre='Fiction',
            name='A. Author', num=50, copies=1,
        )
        self.multi = books.objects.create(
            user=librarian, title='Popular Book', description='d', genre='Fiction',
            name='B. Author', num=51, copies=2,
        )

    def borrow_as(self, user, num, days=7):
        token, _ = Token.objects.get_or_create(user=user)
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
        return self.client.post('/borrow/', {'num': num, 'imprint': 'First', 'due': due_in(days)})

    def test_last_copy_cannot_be_borrowed_twice(self):
        other = User.objects.create_user(username='jules', password='pass12345')
        self.assertEqual(self.borrow_as(self.member, 50).status_code, status.HTTP_201_CREATED)
        response = self.borrow_as(other, 50)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('All copies', response.data['detail'])

    def test_same_member_cannot_borrow_the_same_title_twice(self):
        self.assertEqual(self.borrow_as(self.member, 51).status_code, status.HTTP_201_CREATED)
        response = self.borrow_as(self.member, 51)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('already', response.data['detail'])

    def test_unknown_catalog_number_is_a_404(self):
        response = self.borrow_as(self.member, 9999)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_due_date_beyond_two_weeks_is_rejected(self):
        self.assertEqual(self.borrow_as(self.member, 51, days=30).status_code, status.HTTP_400_BAD_REQUEST)

    def test_due_date_in_the_past_is_rejected(self):
        self.assertEqual(self.borrow_as(self.member, 51, days=-5).status_code, status.HTTP_400_BAD_REQUEST)

    def test_availability_reflects_active_loans(self):
        self.assertEqual(self.client.get('/each/51/').data['available_copies'], 2)
        self.borrow_as(self.member, 51)
        self.assertEqual(self.client.get('/each/51/').data['available_copies'], 1)

    def test_available_filter_hides_fully_loaned_titles(self):
        self.borrow_as(self.member, 50)
        titles = {b['title'] for b in self.client.get('/?available=1').data['results']}
        self.assertNotIn('Rare Book', titles)
        self.assertIn('Popular Book', titles)

    def test_return_frees_the_copy(self):
        loan_id = self.borrow_as(self.member, 50).data['id']
        response = self.client.post(f'/return/{loan_id}/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(borrow.objects.filter(id=loan_id).exists())
        self.assertEqual(self.client.get('/each/50/').data['available_copies'], 1)

    def test_member_cannot_return_someone_elses_loan(self):
        other = User.objects.create_user(username='jules', password='pass12345')
        loan_id = self.borrow_as(other, 51).data['id']
        self.borrow_as(self.member, 50)  # switch credentials back to the member
        response = self.client.post(f'/return/{loan_id}/')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(borrow.objects.filter(id=loan_id).exists())

    def test_search_matches_author_and_isbn(self):
        books.objects.filter(num=50).update(isbn='9780000000001')
        by_author = {b['title'] for b in self.client.get('/lists/?search=A.%20Author').data}
        by_isbn = {b['title'] for b in self.client.get('/lists/?search=9780000000001').data}
        self.assertEqual(by_author, {'Rare Book'})
        self.assertEqual(by_isbn, {'Rare Book'})


class BorrowLimitTests(APITestCase):
    def setUp(self):
        self.reader = User.objects.create_user(username='iris', password='pass12345')
        librarian = User.objects.create_user(username='librarian', password='pass12345')
        self.token = Token.objects.create(user=self.reader)
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {self.token.key}')
        for i in range(6):
            books.objects.create(
                user=librarian, title=f'Book {i}', description='d', genre='Fiction',
                name='Author', num=i, copies=3,
            )
        for i in range(5):
            self.client.post('/borrow/', {'num': i, 'imprint': 'First', 'due': due_in(7)})

    def test_sixth_borrow_is_rejected(self):
        response = self.client.post('/borrow/', {'num': 5, 'imprint': 'First', 'due': due_in(7)})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(borrow.objects.filter(user=self.reader).count(), 5)

    def test_borrow_allowed_after_returning_one(self):
        loan = borrow.objects.filter(user=self.reader).first()
        self.client.post(f'/return/{loan.id}/')
        response = self.client.post('/borrow/', {'num': 5, 'imprint': 'First', 'due': due_in(7)})
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)


class BookPostAuthorizationTests(APITestCase):
    def test_bookp_requires_authentication(self):
        response = self.client.post('/bookp/', {'title': 'New Book', 'num': 99})
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_bookp_requires_admin(self):
        user = User.objects.create_user(username='grace', password='pass12345')
        token = Token.objects.create(user=user)
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
        response = self.client.post('/bookp/', {
            'title': 'New Book', 'genre': 'Fiction', 'description': 'desc', 'num': 99, 'name': 'Grace',
        })
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(books.objects.filter(num=99).exists())

    def test_bookp_attributes_book_to_request_user(self):
        user = User.objects.create_user(username='grace', password='pass12345', is_staff=True)
        token = Token.objects.create(user=user)
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
        response = self.client.post('/bookp/', {
            'title': 'New Book', 'genre': 'Fiction', 'description': 'desc', 'num': 99, 'name': 'Grace',
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        created = books.objects.get(num=99)
        self.assertEqual(created.user, user)

    def test_bookp_accepts_catalog_fields_and_rejects_duplicate_numbers(self):
        admin = User.objects.create_user(username='head_librarian', password='pass12345', is_staff=True)
        token = Token.objects.create(user=admin)
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
        payload = {
            'title': 'Catalogued', 'genre': 'Fiction', 'description': 'desc', 'num': 700,
            'name': 'Some Author', 'isbn': '9780000000002', 'publish_year': 1999, 'pages': 321, 'copies': 4,
        }
        first = self.client.post('/bookp/', payload)
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(first.data['copies'], 4)
        self.assertEqual(first.data['available_copies'], 4)
        second = self.client.post('/bookp/', payload)
        self.assertEqual(second.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('num', second.data)


class GenreFilterTests(APITestCase):
    def setUp(self):
        owner = User.objects.create_user(username='cataloger', password='pass12345')
        books.objects.create(user=owner, title='F1', description='d', genre='Fantasy', name='A', num=901)
        books.objects.create(user=owner, title='F2', description='d', genre='Fantasy', name='A', num=902)
        books.objects.create(user=owner, title='G1', description='d', genre='Gothic', name='A', num=903)

    def test_genre_query_param_filters_case_insensitively(self):
        response = self.client.get('/?genre=fantasy')
        titles = {b['title'] for b in response.data['results']}
        self.assertEqual(titles, {'F1', 'F2'})

    def test_genres_list_returns_counts(self):
        response = self.client.get('/genres/')
        by_genre = {row['genre']: row['book_count'] for row in response.data}
        self.assertEqual(by_genre['Fantasy'], 2)
        self.assertEqual(by_genre['Gothic'], 1)


class BookListPaginationTests(APITestCase):
    def setUp(self):
        owner = User.objects.create_user(username='librarian', password='pass12345')
        self.total = BOOKS_PAGE_SIZE * 2 + 2
        for i in range(self.total):
            books.objects.create(
                user=owner, title=f'Book {i:02}', description='d',
                genre='Fiction', name='Someone', num=300 + i,
            )

    def test_default_page_returns_first_page_metadata(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['page'], 1)
        self.assertEqual(response.data['count'], self.total)
        self.assertEqual(response.data['num_pages'], 3)
        self.assertEqual(len(response.data['results']), BOOKS_PAGE_SIZE)

    def test_second_page_returns_different_books(self):
        page1 = self.client.get('/?page=1').data['results']
        page2 = self.client.get('/?page=2').data['results']
        page1_ids = {b['id'] for b in page1}
        page2_ids = {b['id'] for b in page2}
        self.assertTrue(page1_ids.isdisjoint(page2_ids))

    def test_out_of_range_page_falls_back_to_last_page(self):
        response = self.client.get('/?page=999')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['page'], 3)

    def test_invalid_page_falls_back_to_first_page(self):
        response = self.client.get('/?page=not-a-number')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['page'], 1)


class AuthorListingTests(APITestCase):
    def test_users_without_books_are_not_listed_as_authors(self):
        User.objects.create_user(username='no_books', password='pass12345')
        response = self.client.get('/author/')
        usernames = [a['username'] for a in response.data]
        self.assertNotIn('no_books', usernames)

    def test_author_with_multiple_books_appears_once_with_correct_count(self):
        author_user = User.objects.create_user(username='prolific', password='pass12345')
        for i in range(3):
            books.objects.create(
                user=author_user, title=f'Book {i}', description='d',
                genre='Fiction', name='Prolific Author', num=100 + i,
            )
        response = self.client.get('/author/')
        matches = [a for a in response.data if a['username'] == 'prolific']
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]['book_count'], 3)

    def test_author_detail_returns_all_their_books_in_one_call(self):
        author_user = User.objects.create_user(username='solo', password='pass12345')
        books.objects.create(
            user=author_user, title='Solo Book', description='d',
            genre='Romance', name='Solo Author', num=200,
        )
        response = self.client.get(f'/autbook/{author_user.id}/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['username'], 'solo')
        self.assertEqual(response.data['book_count'], 1)
        self.assertEqual(len(response.data['books']), 1)
        self.assertEqual(response.data['books'][0]['title'], 'Solo Book')

    def test_author_detail_404_for_unknown_user(self):
        response = self.client.get('/autbook/999999/')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


def _verification_link_parts(message):
    """Pull the (uid, token) out of the confirmation link in a sent email."""
    html = message.alternatives[0][0]
    tail = html.split('/verify-email/')[1]
    uid, token = re.split(r'["<\s]', tail)[0].split('/')[:2]
    return uid, token


class BrokenBackend(BaseEmailBackend):
    def send_messages(self, email_messages):
        raise RuntimeError('mail server unreachable')


@override_settings(REQUIRE_EMAIL_VERIFICATION=True)
class EmailVerificationTests(APITestCase):
    def setUp(self):
        cache.clear()  # the resend throttle counts per client in the cache

    def _signup(self, **overrides):
        payload = {'username': 'grace', 'email': 'grace@example.com', 'password': 'strongpass123'}
        payload.update(overrides)
        return self.client.post('/signup/', payload)

    def test_signup_creates_inactive_account_and_sends_link(self):
        response = self._signup()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data['verification_required'])
        self.assertNotIn('token', response.data)
        user = User.objects.get(username='grace')
        self.assertFalse(user.is_active)
        self.assertFalse(Token.objects.filter(user=user).exists())
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['grace@example.com'])
        self.assertIn('/verify-email/', mail.outbox[0].alternatives[0][0])

    def test_emailed_link_activates_the_account(self):
        self._signup()
        uid, token = _verification_link_parts(mail.outbox[0])
        response = self.client.post('/verify-email/', {'uid': uid, 'token': token})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(User.objects.get(username='grace').is_active)
        signin = self.client.post('/signin/', {'username': 'grace', 'password': 'strongpass123'})
        self.assertEqual(signin.status_code, status.HTTP_200_OK)

    def test_using_the_link_twice_is_harmless(self):
        self._signup()
        uid, token = _verification_link_parts(mail.outbox[0])
        self.client.post('/verify-email/', {'uid': uid, 'token': token})
        response = self.client.post('/verify-email/', {'uid': uid, 'token': token})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['code'], 'already_verified')

    def test_garbage_and_tampered_links_are_rejected(self):
        self._signup()
        uid, token = _verification_link_parts(mail.outbox[0])
        tampered = token[:-1] + ('a' if token[-1] != 'a' else 'b')
        for payload in (
            {'uid': uid, 'token': 'nope'},
            {'uid': 'zzz', 'token': token},
            {'uid': '', 'token': ''},
            {'uid': uid, 'token': tampered},
        ):
            response = self.client.post('/verify-email/', payload)
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, payload)
            self.assertEqual(response.data['code'], 'invalid')
        self.assertFalse(User.objects.get(username='grace').is_active)

    def test_password_reset_token_cannot_verify_an_email(self):
        self._signup()
        user = User.objects.get(username='grace')
        response = self.client.post('/verify-email/', {
            'uid': urlsafe_base64_encode(force_bytes(user.pk)),
            'token': default_token_generator.make_token(user),
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(User.objects.get(username='grace').is_active)

    def test_expired_link_is_rejected(self):
        self._signup()
        user = User.objects.get(username='grace')
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = email_verification_token.make_token(user)
        with override_settings(PASSWORD_RESET_TIMEOUT=-1):
            response = self.client.post('/verify-email/', {'uid': uid, 'token': token})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(User.objects.get(username='grace').is_active)

    def test_unverified_signin_gets_a_specific_message(self):
        self._signup()
        response = self.client.post('/signin/', {'username': 'grace', 'password': 'strongpass123'})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data['code'], 'unverified')

    def test_wrong_password_on_unverified_account_stays_generic(self):
        self._signup()
        response = self.client.post('/signin/', {'username': 'grace', 'password': 'wrong-password'})
        self.assertNotEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertNotIn('code', response.data)

    def test_resend_sends_a_fresh_working_link(self):
        self._signup()
        mail.outbox.clear()
        response = self.client.post('/resend-verification/', {'email': 'grace@example.com'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(mail.outbox), 1)
        uid, token = _verification_link_parts(mail.outbox[0])
        self.assertEqual(self.client.post('/verify-email/', {'uid': uid, 'token': token}).status_code, 200)

    def test_resend_accepts_username(self):
        self._signup()
        mail.outbox.clear()
        self.client.post('/resend-verification/', {'username': 'grace'})
        self.assertEqual(len(mail.outbox), 1)

    def test_resend_is_silent_for_unknown_or_already_active_accounts(self):
        User.objects.create_user(username='active', email='active@example.com', password='pass12345')
        for email in ('nobody@example.com', 'active@example.com'):
            response = self.client.post('/resend-verification/', {'email': email})
            self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(mail.outbox), 0)

    def test_resend_is_rate_limited(self):
        statuses = [
            self.client.post('/resend-verification/', {'email': 'nobody@example.com'}).status_code
            for _ in range(11)
        ]
        self.assertEqual(statuses[-1], status.HTTP_429_TOO_MANY_REQUESTS)

    def test_email_failure_does_not_break_signup(self):
        with override_settings(EMAIL_BACKEND='api.tests.BrokenBackend'):
            response = self._signup()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(User.objects.filter(username='grace').exists())


class EmailVerificationOffTests(APITestCase):
    """With verification off (no real mail backend), signup must not strand people."""

    @override_settings(REQUIRE_EMAIL_VERIFICATION=False)
    def test_signup_signs_straight_in(self):
        response = self.client.post('/signup/', {'username': 'hal', 'email': 'hal@example.com', 'password': 'strongpass123'})
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn('token', response.data)
        self.assertTrue(User.objects.get(username='hal').is_active)
        self.assertEqual(len(mail.outbox), 0)


class _FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status = status_code
        self._payload = payload or {}

    def read(self):
        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@override_settings(GMAIL_CLIENT_ID='cid', GMAIL_CLIENT_SECRET='secret', GMAIL_REFRESH_TOKEN='refresh')
class GmailBackendTests(SimpleTestCase):
    def setUp(self):
        gmail_backend._token_cache.update(value=None, expires_at=0.0)
        self.calls = []

    def _urlopen_returning(self, *responses):
        queue = list(responses)

        def urlopen(request, timeout=None):
            self.calls.append(request)
            return queue.pop(0)
        return mock.patch('urllib.request.urlopen', urlopen)

    def _message(self, fail_silently=False):
        connection = gmail_backend.GmailAPIEmailBackend(fail_silently=fail_silently)
        message = mail.EmailMultiAlternatives(
            'Hello', 'plain body', 'Reader <me@gmail.com>', ['you@example.com'], connection=connection,
        )
        message.attach_alternative('<p>html body</p>', 'text/html')
        return message

    def test_exchanges_refresh_token_then_sends_a_base64url_message(self):
        with self._urlopen_returning(
            _FakeResponse(200, {'access_token': 'at-1', 'expires_in': 3600}),
            _FakeResponse(200, {'id': 'm1'}),
        ):
            sent = self._message().send()
        self.assertEqual(sent, 1)
        token_call, send_call = self.calls
        self.assertEqual(token_call.full_url, gmail_backend.TOKEN_URL)
        self.assertIn(b'refresh_token=refresh', token_call.data)
        self.assertEqual(send_call.full_url, gmail_backend.SEND_URL)
        self.assertEqual(send_call.get_header('Authorization'), 'Bearer at-1')
        raw = json.loads(send_call.data)['raw']
        decoded = base64.urlsafe_b64decode(raw + '=' * (-len(raw) % 4)).decode()
        self.assertIn('Subject: Hello', decoded)
        self.assertIn('To: you@example.com', decoded)
        self.assertIn('html body', decoded)

    def test_access_token_is_cached_between_emails(self):
        with self._urlopen_returning(
            _FakeResponse(200, {'access_token': 'at-1', 'expires_in': 3600}),
            _FakeResponse(200, {}),
            _FakeResponse(200, {}),
        ):
            self._message().send()
            self._message().send()
        self.assertEqual(len(self.calls), 3)  # one token exchange, two sends

    def test_stale_access_token_is_refreshed_once(self):
        gmail_backend._token_cache.update(value='stale', expires_at=9e12)
        with self._urlopen_returning(
            _FakeResponse(401, {'error': {'message': 'Invalid Credentials'}}),
            _FakeResponse(200, {'access_token': 'fresh', 'expires_in': 3600}),
            _FakeResponse(200, {}),
        ):
            self.assertEqual(self._message().send(), 1)
        self.assertEqual(self.calls[-1].get_header('Authorization'), 'Bearer fresh')

    def test_revoked_refresh_token_raises_with_a_useful_hint(self):
        with self._urlopen_returning(_FakeResponse(400, {'error': 'invalid_grant'})):
            with self.assertRaisesMessage(gmail_backend.GmailSendError, 'gmail_authorize.py'):
                self._message().send()

    def test_send_failure_raises_unless_fail_silently(self):
        failing = (
            _FakeResponse(200, {'access_token': 'at', 'expires_in': 3600}),
            _FakeResponse(403, {'error': {'message': 'Insufficient Permission'}}),
        )
        with self._urlopen_returning(*failing):
            with self.assertRaisesMessage(gmail_backend.GmailSendError, 'Insufficient Permission'):
                self._message().send()
        gmail_backend._token_cache.update(value=None, expires_at=0.0)
        with self._urlopen_returning(*failing):
            self.assertEqual(self._message(fail_silently=True).send(), 0)

    @override_settings(GMAIL_REFRESH_TOKEN='')
    def test_missing_credentials_are_reported_by_name(self):
        with self.assertRaisesMessage(gmail_backend.GmailSendError, 'GMAIL_REFRESH_TOKEN'):
            self._message().send()
