from django.contrib.auth import authenticate, login
from django.contrib.auth import logout as django_logout
from django.contrib.auth.models import User
from django.db.models import Count
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.authtoken.models import Token
from rest_framework.generics import ListAPIView
from rest_framework.filters import SearchFilter

from .serializers import UserSerializer, BookSerializer, BorrowSerializer, AuthorSerializer, AuthorDetailSerializer
from .models import books, borrow


@api_view(['POST'])
def signup(request):
    serializer = UserSerializer(data=request.data)
    if serializer.is_valid():
        serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)
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
        # Deliberately generic: don't reveal whether the username exists.
        raise AuthenticationFailed('Invalid username or password.')

    login(request, user)
    token, _ = Token.objects.get_or_create(user=user)

    return Response({
        'token': token.key,
        'id': user.id,
        'username': user.username,
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def logout_view(request):
    Token.objects.filter(user=request.user).delete()
    django_logout(request)
    return Response({'message': 'Logged out successfully.'})


BOOKS_PAGE_SIZE = 5


@api_view(['GET'])
def getbook(request):
    alls = books.objects.all().order_by('-id')
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
@permission_classes([IsAuthenticated])
def bookp(request):
    title = request.data.get('title', '')
    genre = request.data.get('genre', '')
    description = request.data.get('description', '')
    num = request.data.get('num', '')
    name = request.data.get('name', '')

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
    )
    serializer = BookSerializer(book)
    return Response(serializer.data, status=status.HTTP_201_CREATED)
