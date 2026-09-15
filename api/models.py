from django.db import models
from django.contrib.auth.models import User

# Create your models here.

class books(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, db_column='users_id')
    title = models.CharField(max_length=30)
    description = models.TextField(max_length=1500)
    genre = models.CharField(max_length=30)
    name = models.CharField(max_length=30, null=True)
    num = models.IntegerField(null=True)
    # Optional link to real cover art; when blank the frontend falls back to a
    # generated genre-tinted cover instead of showing a broken image.
    cover_url = models.URLField(max_length=500, blank=True, null=True)

class borrow(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, db_column='users_id')
    title = models.CharField(max_length=30)
    description = models.TextField(max_length=1500)
    genre = models.CharField(max_length=30)
    name = models.CharField(max_length=30, null=True)
    num = models.IntegerField(null=True)
    imprint = models.CharField(max_length=30)
    due = models.DateField()
    cover_url = models.URLField(max_length=500, blank=True, null=True)