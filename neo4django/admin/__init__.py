from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.contrib.admin.options import ModelAdmin as DjangoModelAdmin
from django.contrib.admin.options import HORIZONTAL, VERTICAL
from django.contrib.admin.options import StackedInline, TabularInline
from django.contrib.admin.sites import AdminSite as DjangoAdminSite
from django.contrib.admin.filters import (ListFilter, SimpleListFilter,
        FieldListFilter, BooleanFieldListFilter, RelatedFieldListFilter,
        ChoicesFieldListFilter, DateFieldListFilter, AllValuesFieldListFilter)
from django.utils.encoding import force_unicode

from ..contenttypes.models import ContentType

# extend ModelAdmin to use our ContentType
class ModelAdmin(DjangoModelAdmin):
    def log_addition(self, request, object):
        from .models import LogEntry, ADDITION
        LogEntry.objects.log_action(
            user_id         = request.user.pk,
            content_type_id = ContentType.objects.get_for_model(object).pk,
            object_id       = object.pk,
            object_repr     = force_unicode(object),
            action_flag     = ADDITION
        )

    def log_change(self, request, object, message):
        from .models import LogEntry, CHANGE
        LogEntry.objects.log_action(
            user_id         = request.user.pk,
            content_type_id = ContentType.objects.get_for_model(object).pk,
            object_id       = object.pk,
            object_repr     = force_unicode(object),
            action_flag     = CHANGE,
            change_message  = message
        )

    def log_deletion(self, request, object, object_repr):
        from .models import LogEntry, DELETION
        LogEntry.objects.log_action(
            user_id         = request.user.pk,
            content_type_id = ContentType.objects.get_for_model(self.model).pk,
            object_id       = object.pk,
            object_repr     = object_repr,
            action_flag     = DELETION
        )

# patch ModelAdmin.render_change_form to use our ContentType
import types
def copy_func(func):
    return types.FunctionType(func.func_code, dict(func.func_globals),
                              name=func.func_name, argdefs=func.func_defaults,
                              closure=func.func_closure)

render_func = copy_func(DjangoModelAdmin.render_change_form.im_func)
render_func.func_globals['ContentType'] = ContentType
ModelAdmin.render_change_form = render_func

# extend AdminSite to not check for other installed dependencies and to use our
# ModelAdmin
class AdminSite(DjangoAdminSite):
    def check_dependencies(self):
        pass

    def register(self, model_or_iterable, admin_class=None, **options):
        return super(AdminSite, self).register(model_or_iterable,
                                               admin_class or ModelAdmin,
                                               **options)

site = AdminSite()

# copy autodiscover and patch it to use our admin site
from django.contrib.admin import autodiscover

autodiscover = copy_func(autodiscover)
autodiscover.func_globals['site'] = site

del types, DjangoModelAdmin, DjangoAdminSite, render_func 
