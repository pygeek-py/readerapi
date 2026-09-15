from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.html import strip_tags


def _send(subject, template_name, context, to_email):
    html_body = render_to_string(template_name, context)
    text_body = strip_tags(html_body)
    message = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[to_email],
    )
    message.attach_alternative(html_body, 'text/html')
    message.send(fail_silently=False)


def send_password_reset_email(user, uidb64, token):
    reset_url = f"{settings.FRONTEND_URL}/reset-password/{uidb64}/{token}"
    _send(
        subject='Reset your Reader password',
        template_name='emails/reset_password.html',
        context={'username': user.username, 'reset_url': reset_url},
        to_email=user.email,
    )
