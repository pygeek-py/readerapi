from rest_framework import serializers
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
        # Accounts stay inactive (and therefore unable to authenticate) until
        # the verification link is clicked.
        instance.is_active = False
        instance.save()
        return instance


class BookSerializer(serializers.ModelSerializer):
    # Only the id is exposed (no nested email/PII); enough for the frontend
    # to link a book to its author's page.
    author_id = serializers.PrimaryKeyRelatedField(source='user', read_only=True)

    class Meta:
        model = books
        fields = ['id', 'title', 'description', 'genre', 'name', 'num', 'author_id', 'cover_url']


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
