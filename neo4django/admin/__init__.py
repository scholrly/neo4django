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
