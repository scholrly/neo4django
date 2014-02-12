from django.contrib.admin import models as admin_models
from django.utils.translation import ugettext_lazy as _
from django.utils.encoding import smart_text

from ..db import models
from ..db.models.manager import NodeModelManager
from ..graph_auth.models import User
from ..decorators import borrows_methods
from ..contenttypes.models import ContentType

ADDITION = admin_models.ADDITION
DELETION = admin_models.DELETION
CHANGE = admin_models.CHANGE


class LogEntryManager(NodeModelManager):
        def log_action(self, user_id, content_type_id, object_id, object_repr,
                       action_flag, change_message=''):
            content_type = ContentType.objects.get(id=content_type_id)
            user = User.objects.get(id=user_id)
            e = self.model.objects.create(user=user, content_type=content_type,
                    object_id=smart_text(object_id), action_flag=action_flag,
                    object_repr=object_repr[:200], change_message=change_message)


LOGENTRY_PASSTHROUGH_METHODS = ('__repr__', '__str__', 'is_addition',
                                'is_change', 'is_deletion', 'get_edited_object',
                                'get_admin_url')

@borrows_methods(admin_models.LogEntry, LOGENTRY_PASSTHROUGH_METHODS)
class LogEntry(models.NodeModel):
    action_flag = models.IntegerProperty() # _('action flag')
    action_time = models.DateTimeProperty(auto_now=True) # _('action time'), 
    # TODO django 1.5 support (user models, #143)
    user = models.Relationship(User, rel_type='completed_by',related_name='completed_by', single=True)
    content_type = models.Relationship(ContentType, single=True, rel_type='content_type')
    object_id = models.StringProperty() # _('object id')
    object_repr = models.StringProperty(max_length=200) # _('object repr'),
    change_message = models.StringProperty() # _('change message')

    objects = LogEntryManager()

    class Meta:
        app_label = 'neo_admin'
        verbose_name = _('log entry')
        verbose_name_plural = _('log entries')
        ordering = ('-action_time',)

