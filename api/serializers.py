from rest_framework import serializers
from django.contrib.auth.models import User
from .models import books, borrow


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'password']
        extra_kwargs = {
            'password': {'write_only': True}
        }

    def create(self, validated_data):
        password = validated_data.pop('password', None)
        instance = self.Meta.model(**validated_data)
        if password is not None:
            instance.set_password(password)
        instance.save()
        return instance


class BookSerializer(serializers.ModelSerializer):
    class Meta:
        model = books
        fields = ['id', 'title', 'description', 'genre', 'name', 'num']


class BorrowSerializer(serializers.ModelSerializer):
    user = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = borrow
        fields = ['id', 'user', 'title', 'description', 'genre', 'name', 'num', 'imprint', 'due']


class AuthorSerializer(serializers.ModelSerializer):
    """A user who has authored at least one book in the library (see api.views.author)."""
    book_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = User
        fields = ['id', 'username', 'book_count']


class AuthorDetailSerializer(serializers.ModelSerializer):
    """A single author plus every book they've authored, in one response."""
    books = BookSerializer(many=True, read_only=True, source='books_set')
    book_count = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ['id', 'username', 'book_count', 'books']

    def get_book_count(self, obj):
        return obj.books_set.count()
