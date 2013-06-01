from django import template

from django.contrib.admin.templatetags import log as admin_log_tags

from ...utils import copy_func
from ..models import LogEntry

register = template.Library()

# extend / patch AdminLogNode to use our LogEntry
render_func = copy_func(admin_log_tags.AdminLogNode.render)
render_func.func_globals['LogEntry'] = LogEntry

class AdminLogNode(admin_log_tags.AdminLogNode):
    render = render_func

# patch the get_admin_log tag to use the new AdminLogNode
get_admin_log = copy_func(admin_log_tags.get_admin_log)
get_admin_log.func_globals['AdminLogNode'] = AdminLogNode
register.tag(get_admin_log)

