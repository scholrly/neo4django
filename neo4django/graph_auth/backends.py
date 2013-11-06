try:
    from django.contrib.auth import get_user_model
    UserModel = get_user_model()
except ImportError:
    from .models import User as UserModel

class NodeModelBackend(object):
    """
    A Neo4j auth backend.
    """
    supports_object_permissions = False
    supports_anonymous_user = False
    supports_inactive_user = False

    def authenticate(self, username=None, password=None):
        try:
            user = UserModel.objects.get(username=username)
            if user is not None and user.check_password(password):
                return user
        except UserModel.DoesNotExist:
            pass

    def get_user(self, user_id):
        try:
            return UserModel.objects.get(id=user_id)
        except UserModel.DoesNotExist:
            return None
