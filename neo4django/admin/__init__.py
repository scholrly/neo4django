from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.contrib.admin.options import ModelAdmin, HORIZONTAL, VERTICAL
from django.contrib.admin.options import StackedInline, TabularInline
from django.contrib.admin.sites import AdminSite as DjangoAdminSite
from django.contrib.admin.filters import (ListFilter, SimpleListFilter,
                                          FieldListFilter, BooleanFieldListFilter, RelatedFieldListFilter,
                                          ChoicesFieldListFilter, DateFieldListFilter, AllValuesFieldListFilter)

class AdminSite(DjangoAdminSite):
    def check_dependencies(self):
        pass

site = AdminSite()

from django.contrib.admin import autodiscover
import types

autodiscover_globals = dict(autodiscover.func_globals)
autodiscover_globals['site'] = site

autodiscover = types.FunctionType(autodiscover.func_code, autodiscover_globals,
                                  name=autodiscover.func_name,
                                  argdefs=autodiscover.func_defaults,
                                  closure=autodiscover.func_closure)

del types, DjangoModelAdmin, DjangoAdminSite
