from django.utils import timezone
from django.conf import settings

from django.contrib.auth import models as django_auth_models

from ..db import models
from ..db.models.manager import NodeModelManager
from ..decorators import borrows_methods

class UserManager(NodeModelManager, django_auth_models.UserManager):
    pass

# all non-overriden methods of DjangoUser are called this way instead.
# inheritance would be preferred, but isn't an option because of conflicting
# metaclasses and weird class side-effects
USER_PASSTHROUGH_METHODS = (
    "__unicode__", "natural_key", "get_absolute_url",
    "is_anonymous", "is_authenticated", "get_full_name", "set_password",
    "check_password", "set_unusable_password", "has_usable_password",
    "get_group_permissions", "get_all_permissions", "has_perm", "has_perms",
    "has_module_perms", "email_user", 'get_profile','get_username')

@borrows_methods(django_auth_models.User, USER_PASSTHROUGH_METHODS)
class User(models.NodeModel):
    objects = UserManager()

    username = models.StringProperty(indexed=True, unique=True)
    first_name = models.StringProperty()
    last_name = models.StringProperty()

    email = models.EmailProperty(indexed=True)
    password = models.StringProperty()

    is_staff = models.BooleanProperty(default=False)
    is_active = models.BooleanProperty(default=False)
    is_superuser = models.BooleanProperty(default=False)

    last_login = models.DateTimeProperty(default=timezone.now())
    date_joined = models.DateTimeProperty(default=timezone.now())

    USERNAME_FIELD = 'username'
    REQUIRED_FIELDS=['email']

