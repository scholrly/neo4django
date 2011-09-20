import re
import datetime
import time

from abc import ABCMeta

from django.utils.translation import ugettext_lazy as _
from django.db.models.fields import NOT_PROVIDED
from django.core import exceptions, validators
from django.utils.encoding import force_unicode

from neo4jrestclient.client import NotFoundError

from neo4django.decorators import transactional
from base import NodeModel
from relationships import Relationship
from neo4django.validators import validate_array, validate_str_array,\
        validate_int_array, ElementValidator
from neo4django.utils import AttrRouter, write_through

MIN_INT = -9223372036854775808
MAX_INT = 9223372036854775807

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

    def __init__(self, indexed=False, indexed_fulltext=False,
                 indexed_range=False, has_own_index = False, unique=False,
                 name=None, editable=True, null=True, blank=True, validators=[],
                 choices=None, error_messages=None, required=False,
                 serialize=True, metadata={}, default=NOT_PROVIDED,
                 **kwargs):
        if unique and not indexed:
            raise ValueError('A unique property must be indexed.')
        self.indexed = self.db_index = indexed
        self.indexed_fulltext = indexed_fulltext
        self.indexed_range = indexed_range
        self.has_own_index = has_own_index
        self.unique = unique
        self.editable = editable
        self.blank = blank
        self.null = null
        self.serialize = serialize
        self.meta = metadata
        self._default = default

        self.__name = name

        self.choices = choices or []

        self.validators = self.default_validators + validators

        messages = {}
        for c in reversed(self.__class__.__mro__):
            messages.update(getattr(c, 'default_error_messages', {}))
        messages.update(error_messages or {})
        self.error_messages = messages

    @property
    def attname(self):
        return self.__name

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

    @classmethod
    def to_neo(cls, value):
        return value

    @classmethod
    def from_python(cls, value):
        """
        A python-centric alias for to_neo()
        """
        return cls.to_neo(value)

    @classmethod
    def from_neo(cls, value):
        return value

    @classmethod
    def to_python(cls, value):
        """
        A python-centric alias for from_neo()
        """
        return cls.from_neo(value)


    @classmethod
    def to_neo_index(cls, value):
        """
        Convert a Python value to how it should be represented in a Neo4j
        index - often, a string. Subclasses that wish to provide indexing
        should override this method.

        If a property intends to support the `indexed_range` option, the values
        returned by this function need to be lexically ordered in the same way
        as how they should be returned by an ascending range query. Properties
        that don't support said option need not be concerned.
        """
        return cls.to_neo(value)

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
        value = self.to_python(value)
        self.validate(value, model_instance)
        self.run_validators(value)
        return value

    def pre_save(self, model_instance, add, attname):
        pass

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
                     'unique',
                     'to_neo',
                     'to_neo_index',
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
                     'meta',
                     'MAX',
                     'MIN',
                    ], self._property)

        self.__class = cls
        self.__propname = propname
        self.__attname = attname
        properties = self._properties_for(cls)
        properties[self.name] = self # XXX: weakref

    attname = name = property(lambda self: self.__attname)

    def _property_type(self):
        return type(self._property)

    def __cmp__(self, other):
        return cmp(self.creation_counter, other.creation_counter)

    @staticmethod
    def __values_of(instance, create=True):
        try:
            values = instance.__values
            for key, prop in BoundProperty._all_properties_for(instance).items(): #XXX: Might be a faster/more elegant way
                if hasattr(prop._property, 'auto_now'):
                    if prop._property.auto_now and prop.__attname not in values:
                        values[prop.__attname] = datetime.datetime.now() #XXX:Setting to None here sets the node's datetime to None
                        if type(prop._property) == DateProperty: #XXX:Kinda gross way to handle it :\
                            values[prop.__attname] = datetime.datetime.date(values[prop.__attname])
        except:
            values = {}
            if create:
                instance.__values = values
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
        if not self.indexed:
            raise TypeError("'%s' is not indexed" % (self.__propname,))
        else:
            return self.__class.index(using)

    def _save_(instance, node, node_is_new):
        values = BoundProperty.__values_of(instance)
        if values:
            properties = BoundProperty._all_properties_for(instance)
            for key, value in values.items():
                prop = properties[key]
                value = prop.pre_save(node, node_is_new, prop.name) or value
                old, value = prop.__set_value(instance, value)
                if prop.indexed:
                    if prop.unique:#TODO empty values? in validators.empty? # and value is not None:
                        try:
                            old_node = prop.index(using=instance.using)[prop.attname][value]
                        except NotFoundError, e:
                            old_node = None
                        if old_node and old_node != node:
                            raise ValueError(
                                "Duplicate index entries for <%s>.%s" %
                                (instance.__class__.__name__,
                                    prop.name))
                    if old is not None:
                        prop.index(using=instance.using).remove(old, node)
                    if value is not None:
                        prop.index(using=instance.using).add(prop.attname, prop.to_neo_index(value), node)
            values.clear()
    NodeModel._save_properties = staticmethod(_save_) #TODO this needs to be revised. I hope there's a better way.
    del _save_

    def __get__(self, instance, cls=None):
        if instance is None: return self
        values = self.__values_of(instance, create=False)
        if self.__propname in values:
            return values[self.__propname]
        else:
            return self.__get_value(instance)

    def __set__(self, instance, value):
        if write_through(instance):
            self.___set_value(instance, value)
        else:
            values = self.__values_of(instance)
            values[self.__propname] = value

    @transactional
    def __get_value(self, instance):
        try:
            underlying = getattr(instance, 'node', None) or getattr(instance, 'relationship', None)
        except: # no node existed
            pass
        else:
            try:
                return self._property.from_neo(underlying[self.__propname])
            except: # no value set on node
                pass
        return self.get_default() # fall through: default value

    def _get_val_from_obj(self, obj):
        return self.__get__(obj)

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
        if not value is None:
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
        if max_length is not None:
            self.validators.append(validators.MaxLengthValidator(max_length))
        if min_length is not None:
            self.validators.append(validators.MinLengthValidator(min_length))

    @classmethod
    def to_neo(cls, value):
        return unicode(value)

class EmailProperty(StringProperty):
    #TODO docstring
    default_validators = [validators.validate_email]

class URLProperty(StringProperty):
    #TODO docstring
    def __init__(self, verify_exists=False, **kwargs):
        kwargs['max_length'] = kwargs.get('max_length', 2083)
        super(URLProperty, self).__init__(**kwargs)
        self.validators.append(validators.URLValidator(verify_exists=verify_exists))

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

    @classmethod
    def to_neo_index(cls, value):
        #for now, we'll just use a fixed-width binary decimal encoding with a
        #'-' for negative and '0' for positive or 0.
        s = str(abs(value))
        if len(s) > 20:
            raise ValueError('Values should be between {0} and {1}.'.format(MIN_INT, MAX_INT))
        return ('-' if value < 0 else '0') + s.zfill(19)

class DateProperty(Property):
    __format = '%Y-%m-%d'

    ansi_date_re = re.compile(r'^\d{4}-\d{1,2}-\d{1,2}$')

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
        Property.__init__(self, **kwargs)

    @classmethod
    def __parse_date_string(cls, value):
        if not cls.ansi_date_re.search(value):
            raise exceptions.ValidationError(cls.default_error_messages['invalid'])
        # Now that we have the date string in YYYY-MM-DD format, check to make
        # sure it's a valid date.
        # We could use time.strptime here and catch errors, but datetime.date
        # produces much friendlier error messages.
        year, month, day = map(int, value.split('-'))
        try:
            value = datetime.date(year, month, day)
        except ValueError, e:
            msg = cls.default_error_messages['invalid_date'] % _(str(e))
            raise exceptions.ValidationError(msg)

        return value

    @classmethod
    def _format_date(cls, value, format_string=None):
        #TODO obviously would prefer strftime, but it couldn't do year < 1900-
        #this should be replaced
        if not format_string:
            format_string = cls.__format
        return format_string.replace('%Y', str(value.year).zfill(4))\
                     .replace('%m', str(value.month).zfill(2))\
                     .replace('%d', str(value.day).zfill(2))

    @classmethod
    def from_neo(cls, value):
        if value is None or value == '':
            return None
        if isinstance(value, datetime.datetime):
            return value.date()
        if isinstance(value, datetime.date):
            return value

        return cls.__parse_date_string(value)

    @classmethod
    def to_neo(cls, value):
        result = None

        if value is None:
            return ''
        if isinstance(value, datetime.datetime):
            result = value
        elif isinstance(value, datetime.date):
            result = value
        else:
            result = cls.__parse_date_string(value)

        return cls._format_date(result)

    def pre_save(self, model_instance, add, attname):
        if self.auto_now or (self.auto_now_add and add):
            value = datetime.date.today()
            setattr(model_instance, attname, value)
            return value
        else:
            return super(DateProperty, self).pre_save(model_instance, add, attname)

class DateTimeProperty(DateProperty):
    __format = '%Y-%m-%d-%H:%M:%S.%f'

    default_error_messages = {
        'invalid': _(u'Enter a valid date/time in YYYY-MM-DD HH:MM[:ss[.uuuuuu]] format.'),
    }

    MAX=datetime.datetime.max
    MIN=datetime.datetime.min

    @classmethod
    def _format_datetime(cls, value):
        time_string = cls._format_date(value, cls.__format)
        return time_string.replace('%H', str(value.hour).zfill(2))\
                          .replace('%M', str(value.minute).zfill(2))\
                          .replace('%S', str(value.second).zfill(2))\
                          .replace('%f', str(value.microsecond).zfill(6))\

    @classmethod
    def __parse_datetime_string(cls, value):
        try: # Try converting with microseconds
            result = datetime.datetime.strptime(value, cls.__format)
        except ValueError:
            try: # Try without microseconds.
                result = datetime.datetime.strptime(value, '%Y-%m-%d %H:%M:%S')
            except ValueError: # Try without hour/minutes.
                try: # Try without seconds.
                    result = datetime.datetime.strptime(value, '%Y-%m-%d %H:%M')
                except ValueError: # Try without hour/minutes/seconds.
                    try:
                        result = datetime.datetime.strptime(value, '%Y-%m-%d')
                    except ValueError:
                        raise exceptions.ValidationError(cls.default_error_messages['invalid'])

        return result

    @classmethod
    def from_neo(cls, value):
        if value is None or value == '':
            return None

        if isinstance(value, datetime.datetime):
            return value
        if isinstance(value, datetime.date):
            return datetime.datetime(value.year, value.month, value.day)

        return cls.__parse_datetime_string(value)

    @classmethod
    def to_neo(cls, value):
        result = None

        if value is None:
            return ''
        if isinstance(value, datetime.datetime):
            result = value
        elif isinstance(value, datetime.date):
            result = datetime.datetime(value.year, value.month, value.day)
        else:
            result = cls.__parse_datetime_string(value)

        return cls._format_datetime(result)

    def pre_save(self, model_instance, add, attname):
        if self.auto_now or (self.auto_now_add and add):
            value = datetime.datetime.now()
            setattr(model_instance, attname, value)
            return value
        else:
            return super(DateTimeProperty, self).pre_save(model_instance, add, attname)

class ArrayProperty(Property):
    __metaclass__ = ABCMeta

    default_validators = [validate_array]

    def __init__(self, *args, **kwargs):
        """
        Keyword arguments:
        per_element_validators -- a list of validators to apply to each element
            of the sequence, or a tuple containing a list of validators and an
            error message, in that order.
        """
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
