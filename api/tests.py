from django.contrib.auth.models import User
from rest_framework.test import APITestCase
from rest_framework import status
from rest_framework.authtoken.models import Token

from .models import books, borrow


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
            genre='Fiction', name='Frank Herbert', num=1,
        )

    def test_borrow_requires_authentication(self):
        response = self.client.post('/borrow/', {
            'title': 'Dune', 'description': 'Desert planet', 'genre': 'Fiction',
            'name': 'Frank Herbert', 'num': 1, 'imprint': 'First', 'due': '2026-01-01',
        })
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_borrow_ignores_client_supplied_user_and_uses_request_user(self):
        other_user = User.objects.create_user(username='mallory', password='pass12345')
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {self.token.key}')
        response = self.client.post('/borrow/', {
            'user': other_user.id,  # attempt to impersonate another user
            'title': 'Dune', 'description': 'Desert planet', 'genre': 'Fiction',
            'name': 'Frank Herbert', 'num': 1, 'imprint': 'First', 'due': '2026-01-01',
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        created = borrow.objects.get(id=response.data['id'])
        self.assertEqual(created.user, self.owner)

    def test_user_cannot_view_another_users_borrowed_books(self):
        other_user = User.objects.create_user(username='mallory', password='pass12345')
        other_token = Token.objects.create(user=other_user)
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {other_token.key}')
        response = self.client.get(f'/userbo/{self.owner.id}/')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class BookPostAuthorizationTests(APITestCase):
    def test_bookp_requires_authentication(self):
        response = self.client.post('/bookp/', {'title': 'New Book', 'num': 99})
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_bookp_attributes_book_to_request_user(self):
        user = User.objects.create_user(username='grace', password='pass12345')
        token = Token.objects.create(user=user)
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
        response = self.client.post('/bookp/', {
            'title': 'New Book', 'genre': 'Fiction', 'description': 'desc', 'num': 99, 'name': 'Grace',
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        created = books.objects.get(num=99)
        self.assertEqual(created.user, user)


class BookListPaginationTests(APITestCase):
    def setUp(self):
        owner = User.objects.create_user(username='librarian', password='pass12345')
        for i in range(12):
            books.objects.create(
                user=owner, title=f'Book {i}', description='d',
                genre='Fiction', name='Someone', num=300 + i,
            )

    def test_default_page_returns_first_page_metadata(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['page'], 1)
        self.assertEqual(response.data['count'], 12)
        self.assertEqual(response.data['num_pages'], 3)
        self.assertEqual(len(response.data['results']), 5)

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
