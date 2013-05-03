from .models import User

class NodeModelBackend(object):
    """
    A Neo4j auth backend.
    """
    supports_object_permissions = False
    supports_anonymous_user = False
    supports_inactive_user = False
    def authenticate(self, username=None, password=None):
        try:
            user = User.objects.get(username=username)
            if user is not None and user.check_password(password):
                return UserAuthAdapter(user)
        except User.DoesNotExist:
            pass

    def get_user(self, user_id):
        return User.objects.get(user_id=user_id)

class UserAuthAdapter(object):
    """
    An adapter that returns the `user_id` property as the User's `id`

    The idea is that we return this specialized subclass from calls to
    django.contrib.auth.authenticate so that when it is passed to
    django.contrib.auth.login, the user_id will be persisted in session
    instead of the actual id value, which we'd prefer the application
    not use significantly.

    Then our get_user method of NodeModelBackend can search the database
    for a User with that user_id.
    """
    def __init__(self, user):
        self.user = user

    @property
    def id(self):
        return self.user_id

    def __getattr__(self, attr):
        return getattr(self.user, attr)

