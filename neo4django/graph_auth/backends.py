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
            UserModel = get_user_model()
            user = UserModel.objects.get(username=username)
            if user is not None and user.check_password(password):
                return user
        except User.DoesNotExist:
            pass

    def get_user(self, user_id):
        UserModel = get_user_model()
        try:
            return UserModel.objects.get(id=user_id)
        except UserModel.DoesNotExist:
            return None
