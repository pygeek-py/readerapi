from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import timedelta
import uuid

# Create your models here.

VERIFICATION_TOKEN_LIFETIME = timedelta(hours=48)


class EmailVerificationToken(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='verification_token')
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    verified_at = models.DateTimeField(null=True, blank=True)

    def is_expired(self):
        return timezone.now() > self.created_at + VERIFICATION_TOKEN_LIFETIME

    def reset(self):
        """Issue a fresh token (used for resend), keeping the same row."""
        self.token = uuid.uuid4()
        self.created_at = timezone.now()
        self.verified_at = None
        self.save(update_fields=['token', 'created_at', 'verified_at'])
        return self

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