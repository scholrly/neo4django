from .models import User
from django.contrib.auth import get_user_model


class NodeModelBackend(object):
    """
    A Neo4j auth backend.
    """
    supports_object_permissions = False
    supports_anonymous_user = False
    supports_inactive_user = False

    def authenticate(self, username=None, password=None):
        try:
            user = get_user_model().objects.get(username=username)
            if user is not None and user.check_password(password):
                return user
        except User.DoesNotExist:
            pass

    def get_user(self, user_id):
        try:
            return User.objects.get(id=user_id)
        except User.DoesNotExist:
            return None
