from django.contrib.admin import models as admin_models

from django.utils.translation import ugettext_lazy as _

from ..db import models
from ..auth.models import User
from ..decorators import borrows_methods

LOGENTRY_PASSTHROUGH_METHODS = ('__repr__', '__str__', 'is_addition',
                                'is_change', 'is_deletion', 'get_edited_object',
                                'get_admin_url')

@borrows_methods(admin_models.LogEntry, LOGENTRY_PASSTHROUGH_METHODS)
class LogEntry(models.NodeModel):
    action_time = models.DateTimeProperty(auto_now=True) # _('action time'), 
    # TODO django 1.5 support (user models, #143)
    user = models.Relationship(User, rel_type='completed_by', single=True)
    #content_type = models.ForeignKey(ContentType, blank=True, null=True)
    object_id = models.StringProperty() # _('object id')
    object_repr = models.StringProperty(max_length=200) # _('object repr'),
    action_flag = models.IntegerProperty() # _('action flag')
    change_message = models.StringProperty() # _('change message')

    objects = admin_models.LogEntryManager()

    class Meta:
        verbose_name = _('log entry')
        verbose_name_plural = _('log entries')
        ordering = ('-action_time',)

