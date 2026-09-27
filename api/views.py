import logging
import threading
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import authenticate, login
from django.contrib.auth import logout as django_logout
from django.contrib.auth.models import User
from django.contrib.auth.tokens import default_token_generator
from django.db import transaction
from django.db.models import Count, F, IntegerField, OuterRef, Subquery, Value
from django.db.models.functions import Coalesce
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.http import JsonResponse
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework.response import Response
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.authtoken.models import Token
from rest_framework.generics import ListAPIView
from rest_framework.filters import SearchFilter
from rest_framework.throttling import AnonRateThrottle

from .emails import send_password_reset_email, send_verification_email
from .models import books, borrow
from .tokens import email_verification_token
from .serializers import UserSerializer, BookSerializer, BorrowSerializer, AuthorSerializer, AuthorDetailSerializer

# Generic response for anything that could otherwise reveal whether an
# email/account exists (resend verification, forgot password).
GENERIC_EMAIL_RESPONSE = {
    'detail': "If an account matches that email, we've sent you a link.",
}


def healthz(request):
    """Plain Django view (no DRF, no DB) for uptime/keep-alive pings."""
    return JsonResponse({'status': 'ok'})


def readyz(request):
    """
    Like healthz, but also touches the database. Render's web service and
    Neon's Postgres compute suspend on separate idle timers; the keep-alive
    workflow pings this (not just healthz) so Neon never gets the chance to
    go cold. A cold Neon compute can take long enough to wake that the first
    real request's query times out and the connection gets reset, which is
    exactly what a signed-out visitor would see as "Unable to reach the
    server" the moment they try to sign up or browse the catalog.
    """
    books.objects.exists()
    return JsonResponse({'status': 'ok', 'db': 'ok'})


logger = logging.getLogger(__name__)


class ResendVerificationThrottle(AnonRateThrottle):
    """Stops the resend endpoint being used to spam an address."""
    scope = 'resend_verification'
    THROTTLE_RATES = {'resend_verification': '10/hour'}


def _send_email_safely(send_fn, *args):
    """
    Email delivery is best-effort from the caller's point of view: a
    misconfigured or unreachable mail provider shouldn't turn account
    creation, resend, or password-reset requests into a 500, and shouldn't
    make the caller wait on it either. The action that already succeeded
    (account created, token issued) still stands; the user can always use
    "resend" once delivery is working. See EMAIL_SEND_IN_BACKGROUND.
    """
    def _run():
        try:
            send_fn(*args)
        except Exception:
            logger.exception('Failed to send email via %s', send_fn.__name__)

    if settings.EMAIL_SEND_IN_BACKGROUND:
        threading.Thread(target=_run, daemon=True).start()
    else:
        _run()


def _send_verification(user):
    uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
    _send_email_safely(send_verification_email, user, uidb64, email_verification_token.make_token(user))


@api_view(['POST'])
def signup(request):
    serializer = UserSerializer(data=request.data)
    if serializer.is_valid():
        user = serializer.save()
        if not user.is_active:
            _send_verification(user)
            return Response(
                {
                    'detail': 'Account created. Check your email to confirm your address before signing in.',
                    'verification_required': True,
                },
                status=status.HTTP_201_CREATED,
            )
        token, _ = Token.objects.get_or_create(user=user)
        return Response(
            {
                'detail': 'Account created.',
                'token': token.key,
                'id': user.id,
                'username': user.username,
                'is_admin': user.is_staff,
            },
            status=status.HTTP_201_CREATED,
        )
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
def signin(request):
    username = request.data.get('username')
    password = request.data.get('password')

    if not username or not password:
        return Response(
            {'detail': 'Username and password are required.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    user = authenticate(request, username=username, password=password)
    if user is None:
        # Right password on an account that hasn't confirmed its email yet:
        # say so specifically (they proved they own the account) so the
        # frontend can offer to resend the link.
        candidate = User.objects.filter(username=username, is_active=False).first()
        if candidate is not None and candidate.has_usable_password() and candidate.check_password(password):
            return Response(
                {'detail': 'Please confirm your email before signing in.', 'code': 'unverified'},
                status=status.HTTP_403_FORBIDDEN,
            )
        # Deliberately generic otherwise: don't reveal whether the username exists.
        raise AuthenticationFailed('Invalid username or password.')

    login(request, user)
    token, _ = Token.objects.get_or_create(user=user)

    return Response({
        'token': token.key,
        'id': user.id,
        'username': user.username,
        'is_admin': user.is_staff,
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def logout_view(request):
    Token.objects.filter(user=request.user).delete()
    django_logout(request)
    return Response({'message': 'Logged out successfully.'})


@api_view(['POST'])
def verify_email(request):
    uidb64 = request.data.get('uid', '')
    verify_token = request.data.get('token', '')

    try:
        user = User.objects.get(pk=force_str(urlsafe_base64_decode(uidb64)))
    except (User.DoesNotExist, ValueError, TypeError, OverflowError):
        user = None

    if user is None or not email_verification_token.check_token(user, verify_token):
        return Response(
            {'detail': 'This confirmation link is invalid or has expired.', 'code': 'invalid'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if user.is_active:
        return Response({'detail': 'This email is already confirmed. You can sign in.', 'code': 'already_verified'})

    user.is_active = True
    user.save(update_fields=['is_active'])
    return Response({'detail': 'Your email is confirmed. You can sign in now.'})


@api_view(['POST'])
@throttle_classes([ResendVerificationThrottle])
def resend_verification(request):
    email = request.data.get('email', '')
    username = request.data.get('username', '')
    user = None
    if email:
        user = User.objects.filter(email__iexact=email).first()
    elif username:
        user = User.objects.filter(username=username).first()

    if user is not None and not user.is_active and user.has_usable_password():
        _send_verification(user)

    return Response(GENERIC_EMAIL_RESPONSE)


@api_view(['POST'])
def password_reset_request(request):
    email = request.data.get('email', '')
    user = User.objects.filter(email__iexact=email, is_active=True).first()

    if user is not None:
        uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
        reset_token = default_token_generator.make_token(user)
        _send_email_safely(send_password_reset_email, user, uidb64, reset_token)

    return Response(GENERIC_EMAIL_RESPONSE)


@api_view(['POST'])
def password_reset_confirm(request):
    uidb64 = request.data.get('uid', '')
    reset_token = request.data.get('token', '')
    new_password = request.data.get('password', '')

    if not uidb64 or not reset_token or not new_password:
        return Response({'detail': 'Missing reset information.'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except (User.DoesNotExist, ValueError, TypeError, OverflowError):
        return Response({'detail': 'This reset link is invalid.', 'code': 'invalid'}, status=status.HTTP_400_BAD_REQUEST)

    if not default_token_generator.check_token(user, reset_token):
        return Response({'detail': 'This reset link is invalid or has expired.', 'code': 'invalid'}, status=status.HTTP_400_BAD_REQUEST)

    if len(new_password) < 8:
        return Response({'detail': 'Password must be at least 8 characters.'}, status=status.HTTP_400_BAD_REQUEST)

    user.set_password(new_password)
    user.save(update_fields=['password'])
    Token.objects.filter(user=user).delete()  # invalidate any existing session token

    return Response({'detail': 'Your password has been reset. You can sign in now.'})


BOOKS_PAGE_SIZE = 12


def with_active_loans(queryset):
    """Annotate each book with how many of its copies are currently on loan (one query)."""
    loans = (
        borrow.objects.filter(num=OuterRef('num'))
        .order_by()
        .values('num')
        .annotate(c=Count('id'))
        .values('c')
    )
    return queryset.annotate(
        active_loans=Coalesce(Subquery(loans, output_field=IntegerField()), Value(0))
    )


@api_view(['GET'])
def getbook(request):
    alls = with_active_loans(books.objects.all()).order_by('title')
    genre = request.query_params.get('genre')
    if genre:
        alls = alls.filter(genre__iexact=genre)
    if request.query_params.get('available') in ('1', 'true'):
        alls = alls.filter(copies__gt=F('active_loans'))
    paginator = Paginator(alls, BOOKS_PAGE_SIZE)

    try:
        page_obj = paginator.page(request.query_params.get('page', 1))
    except (PageNotAnInteger, ValueError):
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    serializer = BookSerializer(page_obj.object_list, many=True)
    return Response({
        'results': serializer.data,
        'page': page_obj.number,
        'num_pages': paginator.num_pages,
        'count': paginator.count,
    })


MAX_ACTIVE_LOANS = 5
MAX_LOAN_DAYS = 14


def _bad_request(message):
    return Response({'detail': message}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def borrows(request):
    try:
        num = int(request.data.get('num'))
    except (TypeError, ValueError):
        return _bad_request('A valid catalog number is required.')

    # Lock the book row so two members can't both take the last copy.
    with transaction.atomic():
        book = books.objects.select_for_update().filter(num=num).first()
        if book is None:
            return Response({'detail': 'That book is not in the catalog.'}, status=status.HTTP_404_NOT_FOUND)

        if borrow.objects.filter(user=request.user).count() >= MAX_ACTIVE_LOANS:
            return _bad_request(
                f'You already have {MAX_ACTIVE_LOANS} books on loan. Return one before borrowing another.'
            )
        if borrow.objects.filter(user=request.user, num=num).exists():
            return _bad_request('You already have this book on loan.')
        if borrow.objects.filter(num=num).count() >= book.copies:
            return _bad_request('All copies of this book are currently on loan.')

        # Title, author, cover etc. always come from the catalog record, never the client.
        serializer = BorrowSerializer(data={
            'title': book.title,
            'description': book.description,
            'genre': book.genre,
            'name': book.name,
            'num': book.num,
            'cover_url': book.cover_url,
            'imprint': request.data.get('imprint'),
            'due': request.data.get('due'),
        })
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        # One day of slack either side so a member in a timezone ahead of or
        # behind the server still passes for "today" and "two weeks from today".
        today = timezone.localdate()
        due = serializer.validated_data['due']
        if due < today - timedelta(days=1):
            return _bad_request('The due date cannot be in the past.')
        if due > today + timedelta(days=MAX_LOAN_DAYS + 1):
            return _bad_request(f'Loans can run for at most {MAX_LOAN_DAYS} days.')

        serializer.save(user=request.user)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def return_book(request, pk):
    """Members can only return their own loans."""
    loan = borrow.objects.filter(pk=pk, user=request.user).first()
    if loan is None:
        return Response({'detail': 'Loan not found.'}, status=status.HTTP_404_NOT_FOUND)
    loan.delete()
    return Response({'detail': 'Thanks, the book has been returned.'})


@api_view(['GET'])
def eachbook(request, pk):
    try:
        book = with_active_loans(books.objects.filter(num=int(pk))).first()
    except (TypeError, ValueError):
        book = None
    if book is None:
        return Response({'detail': 'Book not found.'}, status=status.HTTP_404_NOT_FOUND)
    serializer = BookSerializer(book, many=False)
    return Response(serializer.data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def eachbobook(request, pk):
    """Who has a title out is private: members see only due dates and whether a loan is their own."""
    try:
        loans = borrow.objects.filter(num=int(pk)).order_by('due')
    except (TypeError, ValueError):
        loans = borrow.objects.none()
    return Response([
        {'id': loan.id, 'due': loan.due, 'imprint': loan.imprint, 'mine': loan.user_id == request.user.id}
        for loan in loans
    ])


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def userbo(request, pk):
    if str(request.user.id) != str(pk):
        return Response(
            {'detail': 'You do not have permission to view this user\'s borrowed books.'},
            status=status.HTTP_403_FORBIDDEN,
        )
    book = borrow.objects.filter(user_id=pk)
    serializer = BorrowSerializer(book, many=True)
    return Response(serializer.data)


@api_view(['GET'])
def gens(request):
    alls = books.objects.filter(genre="Fiction")
    serializer = BookSerializer(alls, many=True)
    return Response(serializer.data)


@api_view(['GET'])
def gensr(request):
    alls = books.objects.filter(genre="Romance")
    serializer = BookSerializer(alls, many=True)
    return Response(serializer.data)


@api_view(['GET'])
def genres_list(request):
    """Every genre actually present in the catalog, with how many books are in it."""
    rows = (
        books.objects.values('genre')
        .annotate(book_count=Count('id'))
        .order_by('genre')
    )
    return Response(list(rows))


@api_view(['GET'])
def author(request):
    """Authors are derived from the library: only users with at least one book appear here."""
    first_book_name = books.objects.filter(user=OuterRef('pk')).order_by('id').values('name')[:1]
    users = (
        User.objects.filter(books__isnull=False)
        .annotate(book_count=Count('books'), first_book_name=Subquery(first_book_name))
        .distinct()
        .order_by('username')
    )
    serializer = AuthorSerializer(users, many=True)
    return Response(serializer.data)


@api_view(['GET'])
def autbook(request, pk):
    """Author details plus every book they've authored, in a single response."""
    try:
        author_user = User.objects.get(pk=pk)
    except User.DoesNotExist:
        return Response({'detail': 'Author not found.'}, status=status.HTTP_404_NOT_FOUND)
    serializer = AuthorDetailSerializer(author_user)
    return Response(serializer.data)


class EventListView(ListAPIView):
    """Catalog search: matches title, author name, or ISBN anywhere in the text."""
    serializer_class = BookSerializer
    filter_backends = [SearchFilter]
    search_fields = ['title', 'name', 'isbn']

    def get_queryset(self):
        return with_active_loans(books.objects.all()).order_by('title')


@api_view(['POST'])
@permission_classes([IsAdminUser])
def bookp(request):
    serializer = BookSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    num = serializer.validated_data.get('num')
    if num is None:
        return Response({'num': ['A catalog number is required.']}, status=status.HTTP_400_BAD_REQUEST)
    if books.objects.filter(num=num).exists():
        return Response({'num': ['That catalog number is already in use.']}, status=status.HTTP_400_BAD_REQUEST)

    book = serializer.save(user=request.user)
    return Response(BookSerializer(book).data, status=status.HTTP_201_CREATED)
