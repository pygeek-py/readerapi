import logging

from django.contrib.auth import authenticate, login
from django.contrib.auth import logout as django_logout
from django.contrib.auth.models import User
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Count
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework.response import Response
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.authtoken.models import Token
from rest_framework.generics import ListAPIView
from rest_framework.filters import SearchFilter

from .emails import send_verification_email, send_password_reset_email
from .models import EmailVerificationToken, books, borrow
from .serializers import UserSerializer, BookSerializer, BorrowSerializer, AuthorSerializer, AuthorDetailSerializer

# Generic responses for anything that could otherwise reveal whether an
# email/account exists (resend verification, forgot password).
GENERIC_EMAIL_RESPONSE = {
    'detail': "If an account matches that email, we've sent you a link.",
}

logger = logging.getLogger(__name__)


def _send_email_safely(send_fn, *args):
    """
    Email delivery is best-effort from the caller's point of view: a
    misconfigured or unreachable SMTP server shouldn't turn account
    creation, resend, or password-reset requests into a 500. The action
    that already succeeded (account created, token issued) still stands;
    the user can always use "resend" once delivery is working.
    """
    try:
        send_fn(*args)
    except Exception:
        logger.exception('Failed to send email via %s', send_fn.__name__)


@api_view(['POST'])
def signup(request):
    serializer = UserSerializer(data=request.data)
    if serializer.is_valid():
        user = serializer.save()
        verification = EmailVerificationToken.objects.create(user=user)
        _send_email_safely(send_verification_email, user, verification.token)
        return Response(
            {'detail': 'Account created. Check your email to verify your address before signing in.'},
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

    try:
        candidate = User.objects.get(username=username)
    except User.DoesNotExist:
        candidate = None

    if candidate is not None and candidate.check_password(password) and not candidate.is_active:
        return Response(
            {'detail': 'Please verify your email before signing in.', 'code': 'unverified'},
            status=status.HTTP_403_FORBIDDEN,
        )

    user = authenticate(request, username=username, password=password)
    if user is None:
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
def verify_email(request, token):
    try:
        verification = EmailVerificationToken.objects.get(token=token)
    except (EmailVerificationToken.DoesNotExist, ValueError, DjangoValidationError):
        return Response({'detail': 'This verification link is invalid.', 'code': 'invalid'}, status=status.HTTP_400_BAD_REQUEST)

    if verification.verified_at is not None:
        return Response({'detail': 'This email is already verified. You can sign in.', 'code': 'already_verified'})

    if verification.is_expired():
        return Response({
            'detail': 'This verification link has expired.',
            'code': 'expired',
            'username': verification.user.username,
        }, status=status.HTTP_400_BAD_REQUEST)

    verification.verified_at = timezone.now()
    verification.save(update_fields=['verified_at'])

    user = verification.user
    user.is_active = True
    user.save(update_fields=['is_active'])

    return Response({'detail': 'Your email is verified. You can sign in now.'})


@api_view(['POST'])
def resend_verification(request):
    email = request.data.get('email', '')
    username = request.data.get('username', '')
    user = None
    if email:
        user = User.objects.filter(email__iexact=email).first()
    elif username:
        user = User.objects.filter(username=username).first()

    if user is not None and not user.is_active:
        verification, _ = EmailVerificationToken.objects.get_or_create(user=user)
        verification.reset()
        _send_email_safely(send_verification_email, user, verification.token)

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


BOOKS_PAGE_SIZE = 5


@api_view(['GET'])
def getbook(request):
    alls = books.objects.all().order_by('-id')
    genre = request.query_params.get('genre')
    if genre:
        alls = alls.filter(genre__iexact=genre)
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


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def borrows(request):
    serializer = BorrowSerializer(data=request.data)
    if serializer.is_valid():
        serializer.save(user=request.user)
        return Response(serializer.data, status=status.HTTP_201_CREATED)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET'])
def eachbook(request, pk):
    try:
        book = books.objects.get(num=pk)
    except books.DoesNotExist:
        return Response({'detail': 'Book not found.'}, status=status.HTTP_404_NOT_FOUND)
    serializer = BookSerializer(book, many=False)
    return Response(serializer.data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def eachbobook(request, pk):
    book = borrow.objects.filter(num=pk)
    serializer = BorrowSerializer(book, many=True)
    return Response(serializer.data)


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
    users = (
        User.objects.filter(books__isnull=False)
        .annotate(book_count=Count('books'))
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


@api_view(['GET'])
def userb(request, pk):
    try:
        use = User.objects.get(id=pk)
    except User.DoesNotExist:
        return Response({'detail': 'User not found.'}, status=status.HTTP_404_NOT_FOUND)
    serializer = UserSerializer(use, many=False)
    return Response(serializer.data)


class EventListView(ListAPIView):
    queryset = books.objects.all()
    serializer_class = BookSerializer
    filter_backends = [SearchFilter]
    search_fields = ['^title']


@api_view(['POST'])
@permission_classes([IsAdminUser])
def bookp(request):
    title = request.data.get('title', '')
    genre = request.data.get('genre', '')
    description = request.data.get('description', '')
    num = request.data.get('num', '')
    name = request.data.get('name', '')
    cover_url = request.data.get('cover_url', '') or None

    if not title or not num:
        return Response(
            {'detail': 'title and num are required.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    book = books.objects.create(
        user=request.user,
        title=title,
        genre=genre,
        description=description,
        num=num,
        name=name,
        cover_url=cover_url,
    )
    serializer = BookSerializer(book)
    return Response(serializer.data, status=status.HTTP_201_CREATED)
