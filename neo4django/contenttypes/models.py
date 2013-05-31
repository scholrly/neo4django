from django.utils.translation import ugettext_lazy as _
from django.contrib.contenttypes import models as django_ct_models

from ..db import models
from ..db.models.manager import NodeModelManager
from ..decorators import borrows_methods

class ContentTypeManager(NodeModelManager, django_ct_models.ContentTypeManager):
    pass

CONTENTTYPE_PASSTHROUGH_METHODS = ('model_class', 'get_object_for_this_type',
                                   'get_all_objects_for_this_type', 'natural_key')

@borrows_methods(django_ct_models.ContentType, CONTENTTYPE_PASSTHROUGH_METHODS)
class ContentType(models.NodeModel):
    name = models.StringProperty(max_length=100)
    app_label = models.StringProperty(max_length=100)
    model = models.StringProperty(max_length=100) # _('python model class name')
    # XXX this is a workaround for not yet supporting unique_together
    app_and_model = models.StringProperty(unique=True, indexed=True)

    objects = ContentTypeManager()

    def save(self, *args, **kwargs):
        self.app_and_model = ':'.join((self.app_label, self.model))
        return super(ContentType, self).save(*args, **kwargs)

    class Meta:
        app_label = 'neo_contenttypes'
        verbose_name = _('content type')
        verbose_name_plural = _('content types')
        ordering = ('name',)
        # TODO this is unsupported, currently working around
        unique_together = (('app_label', 'model'),)

