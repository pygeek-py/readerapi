from django.db import models
from django.contrib.auth.models import User

# Create your models here.

class books(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, db_column='users_id')
    title = models.CharField(max_length=200)
    description = models.TextField(max_length=1500)
    genre = models.CharField(max_length=50)
    name = models.CharField(max_length=100, null=True)
    num = models.IntegerField(null=True)
    # Optional link to real cover art; when blank the frontend falls back to a
    # generated genre-tinted cover instead of showing a broken image.
    cover_url = models.URLField(max_length=500, blank=True, null=True)
    isbn = models.CharField(max_length=20, blank=True, null=True)
    publish_year = models.PositiveSmallIntegerField(blank=True, null=True)
    pages = models.PositiveSmallIntegerField(blank=True, null=True)
    # How many physical copies the library holds; availability is copies minus
    # the active loans recorded against this catalog number.
    copies = models.PositiveSmallIntegerField(default=1)

    def __str__(self):
        return f'{self.title} ({self.name})'

class borrow(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, db_column='users_id')
    title = models.CharField(max_length=200)
    description = models.TextField(max_length=1500)
    genre = models.CharField(max_length=50)
    name = models.CharField(max_length=100, null=True)
    num = models.IntegerField(null=True)
    imprint = models.CharField(max_length=100)
    due = models.DateField()
    cover_url = models.URLField(max_length=500, blank=True, null=True)

    def __str__(self):
        return f'{self.title} -> {self.user_id} (due {self.due})'
