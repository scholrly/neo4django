from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.contrib.admin.options import ModelAdmin as DjangoModelAdmin
from django.contrib.admin.options import HORIZONTAL, VERTICAL
from django.contrib.admin.options import StackedInline, TabularInline
from django.contrib.admin.sites import AdminSite as DjangoAdminSite
from django.contrib.admin.filters import (ListFilter, SimpleListFilter,
        FieldListFilter, BooleanFieldListFilter, RelatedFieldListFilter,
        ChoicesFieldListFilter, DateFieldListFilter, AllValuesFieldListFilter)
from django.utils.encoding import force_unicode

from ..utils import copy_func
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

    def get_urls(self):
        from django.core.urlresolvers import RegexURLPattern
        urlpatterns = super(AdminSite, self).get_urls()
        # copy and replace the contenttypes.shortcut pattern
        shortcut_pattern, index = [(p, i) for i, p in enumerate(urlpatterns)
                                   if p.callback and 
                                   p.callback.func_name == 'shortcut'][0]
        callback = copy_func(shortcut_pattern.callback)
        callback.func_globals['ContentType'] = ContentType
        urlpatterns[index] = RegexURLPattern(shortcut_pattern.regex, callback,
                default_args=shortcut_pattern.default_args, name = 'shortcut')
        return urlpatterns
        


site = AdminSite()

# copy autodiscover and patch it to use our admin site
from django.contrib.admin import autodiscover

autodiscover = copy_func(autodiscover)
autodiscover.func_globals['site'] = site

del DjangoModelAdmin, DjangoAdminSite, render_func 
