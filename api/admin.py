from django.contrib import admin
from .models import books, borrow, EmailVerificationToken
# Register your models here.
admin.site.register(books)
admin.site.register(borrow)
admin.site.register(EmailVerificationToken)