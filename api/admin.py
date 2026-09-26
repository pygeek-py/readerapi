from django.contrib import admin
from .models import books, borrow


@admin.register(books)
class BookAdmin(admin.ModelAdmin):
    list_display = ('num', 'title', 'name', 'genre', 'publish_year', 'copies')
    list_filter = ('genre',)
    search_fields = ('title', 'name', 'isbn')
    ordering = ('title',)


@admin.register(borrow)
class LoanAdmin(admin.ModelAdmin):
    list_display = ('title', 'user', 'num', 'due')
    list_filter = ('due',)
    search_fields = ('title', 'user__username')
    ordering = ('due',)
