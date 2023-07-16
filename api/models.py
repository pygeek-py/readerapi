from django.db import models
from django.contrib.auth.models import User
import datetime

# Create your models here.
class books(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, db_column='users_id')
    title = models.CharField(max_length=30)
    description = models.TextField(max_length=1500)
    genre = models.CharField(max_length=30)
    name = models.CharField(max_length=30, null=True)
    num = models.IntegerField(null=True)

class borrow(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, db_column='users_id')
    title = models.CharField(max_length=30)
    description = models.TextField(max_length=1500)
    genre = models.CharField(max_length=30)
    name = models.CharField(max_length=30, null=True)
    num = models.IntegerField(null=True)
    imprint = models.CharField(max_length=30)
    due = models.DateField()