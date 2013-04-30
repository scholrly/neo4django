import functools, types

#import pdb; pdb.set_trace()
from django.contrib.auth import models as django_auth_models
from django.contrib.auth.hashers import check_password, make_password
from django.utils import timezone

from ..db.models.manager import NodeModelManager
from ..db.models import *
from ..decorators import borrows_methods

class UserManager(NodeModelManager, django_auth_models.UserManager):
    pass

# all non-overriden methods of DjangoUser are called this way instead.
# inheritance would be preferred, but isn't an option because of conflicting
# metaclasses and weird class side-effects
USER_PASSTHROUGH_METHODS = ("__unicode__", "natural_key", "get_absolute_url",
        "is_anonymous", "is_authenticated", "get_full_name", "set_password",
        "check_password", "set_unusable_password", "has_usable_password",
        "get_group_permissions", "get_all_permissions", "has_perm", "has_perms",
        "has_module_perms", "email_user", 'get_profile')

@borrows_methods(django_auth_models.User, USER_PASSTHROUGH_METHODS)
class User(NodeModel):
    user_id = AutoProperty()

    username = StringProperty(indexed=True, unique=True)
    first_name = StringProperty()
    last_name = StringProperty()

    email = EmailProperty(indexed=True)
    password = StringProperty()

    is_staff = BooleanProperty(default=False)
    is_active = BooleanProperty(default=False)
    is_superuser = BooleanProperty(default=False)

    last_login = DateTimeProperty(default=timezone.now())
    date_joined = DateTimeProperty(default=timezone.now())

    objects = UserManager()

    class Meta:
       app_label = 'neo_auth'

