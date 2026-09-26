from rest_framework import serializers
from django.conf import settings
from django.contrib.auth.models import User
from .models import books, borrow


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'password']
        extra_kwargs = {
            'password': {'write_only': True},
            'email': {'required': True},
        }

    def validate_email(self, value):
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError('An account with that email already exists.')
        return value

    def create(self, validated_data):
        password = validated_data.pop('password', None)
        instance = self.Meta.model(**validated_data)
        if password is not None:
            instance.set_password(password)
        # When verification is required, the account stays inactive (and so
        # can't authenticate) until the emailed link is followed.
        instance.is_active = not settings.REQUIRE_EMAIL_VERIFICATION
        instance.save()
        return instance


class BookSerializer(serializers.ModelSerializer):
    # Only the id is exposed (no nested email/PII); enough for the frontend
    # to link a book to its author's page.
    author_id = serializers.PrimaryKeyRelatedField(source='user', read_only=True)
    available_copies = serializers.SerializerMethodField()

    class Meta:
        model = books
        fields = [
            'id', 'title', 'description', 'genre', 'name', 'num', 'author_id', 'cover_url',
            'isbn', 'publish_year', 'pages', 'copies', 'available_copies',
        ]

    def get_available_copies(self, obj):
        # List views annotate active_loans in one query; single-object paths
        # (e.g. nested in an author response) fall back to counting.
        loans = getattr(obj, 'active_loans', None)
        if loans is None:
            loans = borrow.objects.filter(num=obj.num).count()
        return max(obj.copies - loans, 0)


class BorrowSerializer(serializers.ModelSerializer):
    user = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = borrow
        fields = ['id', 'user', 'title', 'description', 'genre', 'name', 'num', 'imprint', 'due', 'cover_url']


class AuthorSerializer(serializers.ModelSerializer):
    """A user who has authored at least one book in the library (see api.views.author)."""
    book_count = serializers.IntegerField(read_only=True)
    display_name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ['id', 'username', 'book_count', 'display_name']

    def get_display_name(self, obj):
        # The author list view annotates this in one query; fall back otherwise.
        annotated = getattr(obj, 'first_book_name', None)
        if annotated:
            return annotated
        first_book = obj.books_set.first()
        return first_book.name if first_book else obj.username


class AuthorDetailSerializer(serializers.ModelSerializer):
    """A single author plus every book they've authored, in one response."""
    books = BookSerializer(many=True, read_only=True, source='books_set')
    book_count = serializers.SerializerMethodField()
    display_name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ['id', 'username', 'book_count', 'display_name', 'books']

    def get_book_count(self, obj):
        return obj.books_set.count()

    def get_display_name(self, obj):
        first_book = obj.books_set.first()
        return first_book.name if first_book else obj.username
