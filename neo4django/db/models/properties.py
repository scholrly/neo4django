import re
import datetime
import time

from abc import ABCMeta

from django.utils.translation import ugettext_lazy as _
from django.db.models import fields
from django.db.models.fields import NOT_PROVIDED
from django.core import exceptions, validators
from django.utils.encoding import force_unicode
from django.utils import timezone, datetime_safe
from django.forms import fields as formfields
from django.conf import settings

from neo4jrestclient.client import NotFoundError

from neo4django.decorators import transactional
from .base import NodeModel
from .relationships import Relationship
from .. import connections
from neo4django.validators import validate_array, validate_str_array,\
        validate_int_array, ElementValidator
from neo4django.utils import AttrRouter, write_through
from neo4django.decorators import borrows_methods
from neo4django.constants import ERROR_ATTR

MIN_INT = -9223372036854775808
MAX_INT = 9223372036854775807

FIELD_PASSTHROUGH_METHODS = ('formfield',)

@borrows_methods(fields.Field, FIELD_PASSTHROUGH_METHODS)
class Property(object):
    """Extend to create properties of specific types."""
    # This class borrows heavily from Django 1.3's django.db.models.field.Field

    __metaclass__ = ABCMeta

    default_validators = [] # Default set of validators
    default_error_messages = {
        'invalid_choice': _(u'Value %r is not a valid choice.'),
        'null': _(u'This property cannot be null.'),
        'blank': _(u'This property cannot be blank.'),
    }

    def __init__(self, verbose_name=None, name=None, help_text=None,
                 indexed=False, indexed_fulltext=False, indexed_range=False,
                 indexed_by_member=False, has_own_index=False, unique=False,
                 editable=True, null=True, blank=True, validators=[],
                 choices=None, error_messages=None, required=False,
                 serialize=True, auto=False, metadata={},
                 auto_default=NOT_PROVIDED, default=NOT_PROVIDED, **kwargs):
        if unique and not indexed:
            raise ValueError('A unique property must be indexed.')
        if auto and auto_default == NOT_PROVIDED:
            raise ValueError('Properties with auto=True should also set an '
                             'auto_default.')
        self.indexed = self.db_index = indexed
        self.indexed_fulltext = indexed_fulltext
        self.indexed_range = indexed_range
        self.indexed_by_member = indexed_by_member
        self.has_own_index = has_own_index
        self.unique = unique
        # we don't support this uniqueness granularity
        self.unique_for_date = False
        self.unique_for_month = False
        self.unique_for_year = False
        self.editable = editable
        self.blank = blank
        self.null = null
        self.serialize = serialize
        self.auto = auto
        self.auto_default = auto_default
        self.meta = metadata
        self._default = default

        self.name = self.__name = name
        self.attname = self.name
        self.verbose_name = verbose_name
        self.help_text = help_text

        self.choices = choices or []

        self.validators = self.default_validators + validators

        messages = {}
        for c in reversed(self.__class__.__mro__):
            messages.update(getattr(c, 'default_error_messages', {}))
        messages.update(error_messages or {})
        self.error_messages = messages

    @property
    def default(self):
        self.get_default()

    def has_default(self):
        "Returns a boolean of whether this field has a default value."
        return self._default is not NOT_PROVIDED

    def get_default(self):
        "Returns the default value for this field."
        if self.has_default():
            if callable(self._default):
                return self._default()
            return force_unicode(self._default, strings_only=True)
        return None

    def to_neo(self, value):
        return value

    def from_python(self, value):
        """
        A python-centric alias for to_neo()
        """
        return self.to_neo(value)

    def from_neo(self, value):
        return value

    def to_python(self, value):
        """
        A python-centric alias for from_neo()
        """
        return self.from_neo(value)

    def to_neo_index(self, value):
        """
        Convert a Python value to how it should be represented in a Neo4j
        index - often, a string. Subclasses that wish to provide indexing
        should override this method.

        If a property intends to support the `indexed_range` option, the values
        returned by this function need to be lexically ordered in the same way
        as how they should be returned by an ascending range query. Properties
        that don't support said option need not be concerned.
        """
        return self.to_neo(value)

    def contribute_to_class(self, cls, name):
        """
        Set up properties when the owner class is loaded.
        """
        self.creation_counter = cls.creation_counter
        if issubclass(cls, NodeModel):
            prop = BoundProperty(self, cls, self.__name or name, name)
            cls._meta.add_field(prop)
        elif issubclass(cls, Relationship):
            if self.indexed:
                raise TypeError(
                    "Relationship properties may not be indexed.")
            prop = BoundProperty(self, cls, self.__name or name)
            cls.add_field(prop)
        else:
            raise TypeError("Properties may only be added to Nodes"
                            " or Relationships")
        setattr(cls, name, prop)

    def run_validators(self, value):
        if value in validators.EMPTY_VALUES: #TODO ??? - ML
            return

        errors = []
        for v in self.validators:
            try:
                v(value)
            except exceptions.ValidationError, e:
                if hasattr(e, 'code') and e.code in self.error_messages:
                    message = self.error_messages[e.code]
                    if e.params:
                        message = message % e.params
                    errors.append(message)
                else:
                    errors.extend(e.messages)
        if errors:
            raise exceptions.ValidationError(errors)

    def validate(self, value, model_instance):
        """
        Validates value and throws ValidationError. Subclasses should override
        this to provide validation logic.
        """
        if not self.editable:
            # Skip validation for non-editable fields.
            return
        if self.choices and value:
            for option_key, option_value in self.choices:
                if isinstance(option_value, (list, tuple)):
                    # This is an optgroup, so look inside the group for options.
                    for optgroup_key, optgroup_value in option_value:
                        if value == optgroup_key:
                            return
                elif value == option_key:
                    return
            raise exceptions.ValidationError(self.error_messages['invalid_choice'] % value)

        if value is None and not self.null:
            raise exceptions.ValidationError(self.error_messages['null'])

        if not self.blank and value in validators.EMPTY_VALUES:
            raise exceptions.ValidationError(self.error_messages['blank'])

    def clean(self, value, model_instance):
        """
        Convert the value's type and run validation. Validation errors from to_python
        and validate are propagated. The correct value is returned if no error is
        raised.
        """
        self.validate(value, model_instance)
        self.run_validators(value)
        value = self.to_python(value)
        return value

    def pre_save(self, model_instance, add, attname):
        pass

@borrows_methods(fields.Field, ('save_form_data',))
class BoundProperty(AttrRouter):
    rel = None
    primary_key = False
    def __init__(self, prop, cls, propname, attname, *args, **kwargs):
        super(BoundProperty, self).__init__(*args, **kwargs)
        self._property = prop

        self._route(['creation_counter',
                     'choices',
                     'convert',
                     'indexed',
                     'db_index',
                     'indexed_fulltext',
                     'indexed_range',
                     'indexed_by_member',
                     'unique',
                     'to_neo',
                     'to_neo_index',
                     'to_neo_index_gremlin',
                     'member_to_neo_index',
                     'from_python',
                     'from_neo',
                     'to_python',
                     'default',
                     'has_default',
                     'get_default',
                     'clean',
                     'validate',
                     'run_validators',
                     'pre_save',
                     'serialize',
                     'auto',
                     'auto_default',
                     'next_value',
                     'next_value_gremlin',
                     'meta',
                     'MAX',
                     'MIN',
                     #form-related properties
                     'editable',
                     'blank',
                     'formfield',
                     'unique_for_date',
                     'unique_for_month',
                     'unique_for_year',
                    ], self._property)

        self.__class = cls
        self.__propname = propname
        self.__attname = attname

        # TODO - i don't know why, but this is the final straw. properties
        # and boundproperties need to be merged, the coupling is ridiculous
        self._property.name = propname
        self._property.attname = attname

        properties = self._properties_for(cls)
        properties[self.name] = self # XXX: weakref

    attname = name = property(lambda self: self.__attname)
    target = property(lambda self: self.__class)

    def _property_type(self):
        return type(self._property)

    def __cmp__(self, other):
        return cmp(self.creation_counter, other.creation_counter)

    @staticmethod
    def _values_of(instance, create=True):
        try:
            values = instance._prop_values
        except:
            values = {}
            if create:
                instance._prop_values = values
        return values

    @staticmethod
    def _properties_for(obj_or_cls):
        meta = obj_or_cls._meta
        try:
            properties = meta._properties
        except:
            meta._properties = properties = {}
        return properties

    @staticmethod
    def _all_properties_for(obj_or_cls):
        new_property_dict = {}
        new_property_dict.update(BoundProperty._properties_for(obj_or_cls))

        cls = obj_or_cls if isinstance(obj_or_cls, type) else type(obj_or_cls)

        for parent in cls.__bases__:
            if hasattr(parent, '_meta'):
                new_property_dict.update(BoundProperty._all_properties_for(parent))

        return new_property_dict

    def index(self, using):
        if not (self.indexed or self.auto):
            raise TypeError("'%s' is not indexed" % (self.__propname,))
        else:
            return self.__class.index(using)

    def index_name(self, using):
        if not (self.indexed or self.auto):
            raise TypeError("'%s' is not indexed" % (self.__propname,))
        else:
            return self.__class.index_name(using)

    #update the state of the model instance based on a rest client element property dictionary
    def _update_values_from_dict(instance, new_val_dict, clear=False):
        values = BoundProperty._values_of(instance)
        properties = BoundProperty._all_properties_for(instance)

        values.clear()
        
        for k, v in new_val_dict.items():
            prop = properties.get(k, None)
            if prop:
                #XXX duplicates __get_value()...
                values[k] = prop.to_python(v)
        #XXX this relies on neo4jrestclient private implementation details
        if clear:
            instance.node._dic['data'].clear()
        instance.node._dic['data'].update(new_val_dict)

    #TODO this needs to be revised
    NodeModel._update_values_from_dict = staticmethod(_update_values_from_dict) 
    del _update_values_from_dict

    def _save_(instance, node, node_is_new):
        values = BoundProperty._values_of(instance)
        properties = BoundProperty._all_properties_for(instance)

        gremlin_props = {}
        for key, prop in properties.items():
            prop_class = prop.__class
            prop_dict = gremlin_props[key] = {}
            if prop.auto and values.get(key, None) is None:
                prop_dict['auto_increment'] = True
                prop_dict['increment_func'] = prop.next_value_gremlin
                prop_dict['auto_default'] = prop.auto_default
                prop_dict['auto_abstract'] = prop_class._meta.abstract
                prop_dict['auto_app_label'] = prop_class._meta.app_label
                prop_dict['auto_model'] = prop_class.__name__
            if prop.indexed:
                prop_dict['index_name'] = prop.index_name(instance.using)
                if hasattr(prop, 'to_neo_index_gremlin'):
                    prop_dict['to_index_func'] = prop.to_neo_index_gremlin
            if key in values:
                value = values[key]
                prop.clean(value, instance)
                value = prop.pre_save(node, node_is_new, prop.name) or value
                if (not value in validators.EMPTY_VALUES or
                    getattr(prop._property, "use_string", False)):
                    #should already have errored if self.null==False
                    value = prop.to_neo(value)
                prop_dict['value'] = value
                if prop.indexed:
                    indexed_values = prop_dict['values_to_index'] = []
                    prop_dict['unique'] = bool(prop.unique)
                    if value is not None:
                        indexed_values.append(prop.to_neo_index(values[key]))
                        if prop.indexed_by_member:
                            for m in value:
                                indexed_values.append(prop.member_to_neo_index(m))
                values[key] = value
        script = '''
        node=g.v(nodeId);
        results = Neo4Django.updateNodeProperties(node, propMap);
        '''
        conn = connections[instance.using]
        script_rv= conn.gremlin_tx(script, nodeId=instance.id,
                propMap=gremlin_props, raw=True)

        if (isinstance(script_rv, dict) and ERROR_ATTR in script_rv
            and 'property' in script_rv):
            raise ValueError("Duplicate index entries for <%s>.%s" % 
                                (instance.__class__.__name__,
                                script_rv['property']))
        elif isinstance(script_rv, dict) and 'data' in script_rv:
            #returned a node (TODO #128 error passing generalization)
            NodeModel._update_values_from_dict(instance, script_rv['data'],
                                               clear=True)
        else:
            raise ValueError('Unexpected response from server: %s' %
                             str(script_rv))
        
    #TODO this needs to be revised. I hope there's a better way.
    NodeModel._save_properties = staticmethod(_save_) 
    del _save_

    def __get__(self, instance, cls=None):
        if instance is None: return self
        values = self._values_of(instance, create=False)
        if self.__propname in values:
            return values[self.__propname]
        else:
            return self.__get_value(instance)

    def __set__(self, instance, value):
        if write_through(instance):
            self.___set_value(instance, value)
        else:
            values = self._values_of(instance)
            values[self.__propname] = value

    @transactional
    def __get_value(self, instance):
        try:
            underlying = getattr(instance, 'node', None) or getattr(instance, 'relationship', None)
        except: # no node existed
            pass
        else:
            try:
                values = BoundProperty._values_of(instance)
                values[self.__propname] = val = self._property.to_python(underlying[self.__propname])
                return val
            except: # no value set on node
                pass
        return self.get_default() # fall through: default value

    def _get_val_from_obj(self, obj):
        return self.__get__(obj)

    def value_from_object(self, obj):
        return self._get_val_from_obj(obj)

    def value_to_string(self, obj):
        #TODO not sure if this method plays a bigger role in django
        return str(self.__get__(obj))

    @transactional
    def __set_value(self, instance, value):
        underlying = getattr(instance, 'node', None) or \
                getattr(instance, 'relationship', None)
        if not underlying:
            raise TypeError('Property has no underlying node or relationship!')
        try:
            old = underlying[self.__propname]
        except:
            old = None
        self._property.clean(value, instance)
        #supports null properties
        if not value in validators.EMPTY_VALUES:
            #should already have errored if self.null==False
            value = self._property.to_neo(value)
            underlying[self.__propname] = value
        elif self.__propname in underlying:
            #remove the property from the node if the val is None
            del underlying[self.__propname]


        return (old, value)

class StringProperty(Property):
    #since strings don't have a natural max, this is an arbitrarily high utf-8
    #string. this is necessary for gt string queries, since Lucene range
    #queries (prior 4.0) don't support open-ended ranges
    MAX = u'\U0010FFFF' * 20
    MIN = u''

    def __init__(self, max_length=None, min_length=None, **kwargs):
        if kwargs.get('indexed', False):
            kwargs.setdefault('indexed_fulltext', True)
            kwargs.setdefault('indexed_range', True)
        super(StringProperty, self).__init__(**kwargs)
        self.max_length = max_length
        self.min_length = min_length
        if max_length is not None:
            self.validators.append(validators.MaxLengthValidator(max_length))
        if min_length is not None:
            self.validators.append(validators.MinLengthValidator(min_length))

    def to_neo(cls, value):
        return unicode(value)

    def formfield(self, **kwargs):
        defaults = dict(kwargs)
        if self.max_length is not None:
            defaults['max_length'] = self.max_length
        return super(StringProperty, self).formfield(**defaults)

class EmailProperty(StringProperty):
    #TODO docstring
    default_validators = [validators.validate_email]

    formfield = formfields.EmailField

class URLProperty(StringProperty):
    #TODO docstring
    def __init__(self, verify_exists=False, **kwargs):
        kwargs['max_length'] = kwargs.get('max_length', 2083)
        super(URLProperty, self).__init__(**kwargs)
        self.validators.append(validators.URLValidator(verify_exists=verify_exists))

    def formfield(self, **kwargs):
        defaults = {'form_class':formfields.URLField}
        defaults.update(kwargs)
        return super(URLProperty, self).formfield(**defaults)

class IntegerProperty(Property):
    """
    A 64-bit integer, akin to Django's `BigIntegerField`.
    """
    default_validators = [validators.MinValueValidator(MIN_INT), validators.MaxValueValidator(MAX_INT)]

    MAX = MAX_INT
    MIN = MIN_INT

    def __init__(self, **kwargs):
        if kwargs.get('indexed', False):
            kwargs.setdefault('indexed_fulltext', True)
            kwargs.setdefault('indexed_range', True)
        return super(IntegerProperty, self).__init__(**kwargs)

    def get_default(self):
        return 0

    def to_neo(self, value):
        return int(value)

    def to_neo_index(self, value):
        #for now, we'll just use a fixed-width binary decimal encoding with a
        #'-' for negative and '0' for positive or 0.
        s = str(abs(value))
        if len(s) > 20:
            raise ValueError('Values should be between {0} and {1}.'.format(MIN_INT, MAX_INT))
        return ('-' if value < 0 else '0') + s.zfill(19)

    @property
    def to_neo_index_gremlin(self):
        """
        Return a Gremlin/Groovy closure literal that can compute 
        to_neo_index(value) server-side. The closure should take a single value
        as an argument (that value actually set on the node).
        """
        return """{ i -> (i < 0?'-':'0') + String.format('%019d',i)} """

    def formfield(self, **kwargs):
        defaults = {'form_class':formfields.IntegerField}
        defaults.update(kwargs)
        return super(IntegerProperty, self).formfield(**defaults)

class AutoProperty(IntegerProperty):
    editable = False

    def __init__(self, *args, **kwargs):
        kwargs['auto'] = True
        kwargs['auto_default'] = 1
        super(AutoProperty, self).__init__(*args, **kwargs)

    def get_default(self):
        return None

    def next_value(self, old_value):
        return old_value + 1

    @property
    def next_value_gremlin(self):
        """
        Return a Gremlin/Groovy closure literal that can compute next_value()
        server-side. The closure take a single value as an argument to
        increment.
        """
        return """{ i -> i + 1}"""

    def formfield(self, **kwargs):
        return None

@borrows_methods(fields.DateField, ('to_python',))
class DateProperty(Property):

    default_error_messages = {
        'invalid': _('Enter a valid date in YYYY-MM-DD format.'),
        'invalid_date': _('Invalid date: %s'),
    }

    MAX=datetime.date.max
    MIN=datetime.date.min

    def __init__(self, auto_now=False, auto_now_add=False, **kwargs):
        self.auto_now, self.auto_now_add = auto_now, auto_now_add
        #HACKs : auto_now_add/auto_now should be done as a default or a pre_save.
        if auto_now or auto_now_add:
            kwargs['editable'] = False
            kwargs['blank'] = True
        if kwargs.get('indexed', False):
            kwargs['indexed_range'] = True
        super(DateProperty, self).__init__(**kwargs)

    def to_neo(self, value):
        if value is None:
            return value
        elif isinstance(value, datetime.datetime):
            return value.date().isoformat()
        elif isinstance(value, datetime.date):
            return value.isoformat()
        else:
            return unicode(value)

    def pre_save(self, model_instance, add, attname):
        if self.auto_now or (self.auto_now_add and add):
            value = datetime.date.today()
            setattr(model_instance, attname, value)
            return value
        else:
            return super(DateProperty, self).pre_save(model_instance, add,
                                                      attname)

@borrows_methods(fields.DateTimeField, ('to_python',))
class DateTimeProperty(DateProperty):
    default_error_messages = fields.DateTimeField.default_error_messages

    MAX=datetime.datetime.max
    MIN=datetime.datetime.min

    def to_neo(self, value):
        if value is None:
            return value
        elif isinstance(value, datetime.date):
            if not isinstance(value, datetime.datetime):
                value = datetime_safe.new_datetime(value)
            if settings.USE_TZ and timezone.is_naive(value):
                default_timezone = timezone.get_default_timezone()
                value = timezone.make_aware(value, default_timezone)
            return value.isoformat()
        else:
            # TODO raise error
            pass

    def to_neo_index(self, value):
        cleaned = self.to_neo(value)
        if cleaned is not None:
            return cleaned.replace(' ','-')

    def pre_save(self, model_instance, add, attname):
        if self.auto_now or (self.auto_now_add and add):
            value = timezone.now()
            setattr(model_instance, attname, value)
            return value
        else:
            return super(DateProperty, self).pre_save(model_instance, add,
                                                      attname)

class ArrayProperty(Property):
    __metaclass__ = ABCMeta

    default_validators = [validate_array]

    member_to_neo_index = Property.to_neo_index.im_func

    def __init__(self, *args, **kwargs):
        """
        Keyword arguments:
        per_element_validators -- a list of validators to apply to each element
            of the sequence, or a tuple containing a list of validators and an
            error message, in that order.
        """
        if kwargs.get('indexed', False):
            if 'indexed_by_member' not in kwargs:
                kwargs['indexed_by_member'] = True
        super(ArrayProperty, self).__init__(*args, **kwargs)
        per_key = 'per_element_validators'
        if per_key in kwargs:
            vals_or_tuple = kwargs[per_key]
            if isinstance(vals_or_tuple, tuple):
                per_vals, message = vals_or_tuple
                el_val = ElementValidator(per_vals, message=message)
            else:
                el_val = ElementValidator(vals_or_tuple)
            self.validators.append(el_val)

        #Store array values as a token separated string. For use in the event
        #the user needs to access the neo4j data multiple ways.
        #For example using REST interface you cannot store an empty array
        self.use_string = kwargs.get("use_string", False)
        self.token = kwargs.get("token", ",")
        self.escape_token = kwargs.get("escape_token", "+")
        self.token_regex = "(?<!%s)%s" % (re.escape(self.escape_token), self.token)

    def get_default(self):
        if self.use_string:
            return ""
        else:
            return []

    def from_neo(self, value):
        if value and not isinstance(value, (tuple, list)) and self.use_string:
            array_values = re.split(self.token_regex, value)
            for i, v in enumerate(array_values):
                array_values[i] = v.replace(
                    "%s%s" % (self.escape_token, self.token), self.token)
            return tuple(array_values)
        if not value:
            return tuple([])
        else:
            return tuple(value)

    def to_neo(self, value):
        if self.use_string:
            escaped_values = []
            for v in value:
                escaped_values.append(
                    str(v).replace(self.token, "%s%s" % (self.escape_token,
                                                         self.token)))
            return self.token.join(escaped_values)
        return value

class StringArrayProperty(ArrayProperty):
    default_validators = [validate_str_array]

class URLArrayProperty(StringArrayProperty):
    def __init__(self, *args, **kwargs):
        per_key = 'per_element_validators'
        per_val = validators.URLValidator()
        if per_key in kwargs:
            kwargs[per_key].append(per_val) #TODO make this consistent with super
        else:
            kwargs[per_key] = ([per_val], 'Enter a valid sequence of URLs')
        super(URLArrayProperty, self).__init__(*args, **kwargs)

class IntArrayProperty(ArrayProperty):
    default_validators = [validate_int_array]

    member_to_neo_index = IntegerProperty.to_neo_index.im_func

class BooleanProperty(Property):
    def to_neo(self, value):
        return bool(value)

    def from_neo(self, value):
        return bool(value)
