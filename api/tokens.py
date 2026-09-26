from django.contrib.auth.tokens import PasswordResetTokenGenerator


class EmailVerificationTokenGenerator(PasswordResetTokenGenerator):
    """
    Signed, stateless tokens for the "confirm your email" link. They are tied
    to the user's id, password hash and email, so changing any of those
    invalidates an outstanding link, and they expire after
    settings.PASSWORD_RESET_TIMEOUT. A distinct salt keeps a verification
    token from ever being accepted as a password-reset token, or vice versa.
    """
    key_salt = 'api.tokens.EmailVerificationTokenGenerator'


email_verification_token = EmailVerificationTokenGenerator()
