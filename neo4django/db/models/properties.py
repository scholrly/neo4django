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
from .base import NodeModel
from .relationships import Relationship
from .. import connections
from neo4django.validators import validate_array, validate_str_array,\
        validate_int_array, ElementValidator
from neo4django.utils import AttrRouter, write_through
from neo4django.constants import AUTO_PROP_INDEX_VALUE

try:
    from dateutil.tz import tzutc, tzoffset
except ImportError:
    # Time zone support requires dateutil.tz.tzutc and dateutil.tz.tzoffset, so
    # redefining them here. Since the code is borrowed directly from dateutil
    # (which is BSD-licensed), including the licensing block here too.

    # Copyright 2003-2007 Gustavo Niemeyer <gustavo@niemeyer.net>. All rights
    # reserved.
    #
    # Redistribution and use in source and binary forms, with or without modi-
    # fication, are permitted provided that the following conditions are met:
    #
    #    1. Redistributions of source code must retain the above copyright
    #    notice, this list of conditions and the following disclaimer.
    #
    #    2. Redistributions in binary form must reproduce the above copyright
    #    notice, this list of conditions and the following disclaimer in the
    #    documentation and/or other materials provided with the distribution.
    #
    # THIS SOFTWARE IS PROVIDED BY GUSTAVO NIEMEYER ''AS IS'' AND ANY EXPRESS
    # OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
    # WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
    # DISCLAIMED. IN NO EVENT SHALL GUSTAVO NIEMEYER OR CONTRIBUTORS BE
    # LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
    # CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
    # SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
    # INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
    # CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
    # ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF
    # THE POSSIBILITY OF SUCH DAMAGE.
    #
    # The views and conclusions contained in the software and documentation are
    # those of the authors and should not be interpreted as representing
    # official policies, either expressed or implied, of Gustavo Niemeyer.

    ZERO = datetime.timedelta(0)
    EPOCHORDINAL = datetime.datetime.utcfromtimestamp(0).toordinal()

    class tzutc(datetime.tzinfo):
        def utcoffset(self, dt):
            return ZERO

        def dst(self, dt):
            return ZERO

        def tzname(self, dt):
            return "UTC"

        def __eq__(self, other):
            return (isinstance(other, tzutc) or
                    (isinstance(other, tzoffset) and other._offset == ZERO))

        def __ne__(self, other):
            return not self.__eq__(other)

        def __repr__(self):
            return "%s()" % self.__class__.__name__

        __reduce__ = object.__reduce__


    class tzoffset(datetime.tzinfo):
        def __init__(self, name, offset):
            self._name = name
            self._offset = datetime.timedelta(seconds=offset)

        def utcoffset(self, dt):
            return self._offset

        def dst(self, dt):
            return ZERO

        def tzname(self, dt):
            return self._name

        def __eq__(self, other):
            return (isinstance(other, tzoffset) and
                    self._offset == other._offset)

        def __ne__(self, other):
            return not self.__eq__(other)

        def __repr__(self):
            total_seconds = self._offset.days * 86400 + self._offset.seconds
            return "%s(%s, %s)" % (self.__class__.__name__,
                                   repr(self._name),
                                   total_seconds)

        __reduce__ = object.__reduce__


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
                 indexed_range=False, indexed_by_member=False,
                 has_own_index=False, unique=False, name=None, editable=True,
                 null=True, blank=True, validators=[], choices=None,
                 error_messages=None, required=False, serialize=True,
                 auto=False, metadata={}, auto_default=NOT_PROVIDED,
                 default=NOT_PROVIDED, **kwargs):
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
        self.editable = editable
        self.blank = blank
        self.null = null
        self.serialize = serialize
        self.auto = auto
        self.auto_default = auto_default
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
                     'indexed_by_member',
                     'unique',
                     'to_neo',
                     'to_neo_index',
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
                    ], self._property)

        self.__class = cls
        self.__propname = propname
        self.__attname = attname
        properties = self._properties_for(cls)
        properties[self.name] = self # XXX: weakref

    attname = name = property(lambda self: self.__attname)
    target = property(lambda self: self.__class)

    def _property_type(self):
        return type(self._property)

    def __cmp__(self, other):
        return cmp(self.creation_counter, other.creation_counter)

    @staticmethod
    def __values_of(instance, create=True):
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

    def _save_(instance, node, node_is_new): #TODO this entire method could be transactional
        values = BoundProperty.__values_of(instance)
        properties = BoundProperty._all_properties_for(instance)
        for key, prop in properties.items():
            index = None
            if prop.auto and values.get(key, None) is None:
                type_node = prop.target._type_node(instance.using)

                last_auto_attname = '%s.%s' % (prop.attname, AUTO_PROP_INDEX_VALUE)

                script = prop.next_value_gremlin
                script += \
                """
                typeNode = g.v(typeNodeID)
                rawTypeNode = typeNode.getRawVertex()

                lockManager.getWriteLock(rawTypeNode)

                if (lastAutoProp in typeNode.map) {
                    value = typeNode[lastAutoProp]
                    value = nextValue(value)
                }
                else {
                    value = defaultAutoVal
                }
                typeNode[lastAutoProp] = value

                lockManager.releaseWriteLock(rawTypeNode, null)

                results = value
                """
                conn = connections[instance.using]
                value = conn.gremlin_tx_deadlock_proof(script, 0,
                      defaultAutoVal=prop.auto_default, lastAutoProp=last_auto_attname,
                      typeNodeID=type_node.id)
                #if the code is interrupted between here and setting
                #the value, there might be a problem... worst case, lost
                #id space?

                values[key] = value
            if key in values:
                value = values[key]
                value = prop.pre_save(node, node_is_new, prop.name) or value
                values[key] = value
                old, value = prop.__set_value(instance, value)
                if prop.indexed:
                    index = index if index else prop.index(using=instance.using)
                    if prop.unique:#TODO empty values? in validators.empty? # and value is not None:
                        try:
                            old_node = index[prop.attname][value]
                        except NotFoundError, e:
                            old_node = None
                        if old_node and old_node != node:
                            raise ValueError(
                                "Duplicate index entries for <%s>.%s" %
                                (instance.__class__.__name__,
                                    prop.name))
                    if old is not None:
                        index.delete(prop.attname, None, node)
                    if value is not None:
                        index.add(prop.attname, prop.to_neo_index(value), node)
                        if prop.indexed_by_member:
                            for m in value:
                                index.add(prop.attname, prop.member_to_neo_index(m), node)
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
                values = BoundProperty.__values_of(instance)
                values[self.__propname] = val = self._property.from_neo(underlying[self.__propname])
                return val
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
        if max_length is not None:
            self.validators.append(validators.MaxLengthValidator(max_length))
        if min_length is not None:
            self.validators.append(validators.MinLengthValidator(min_length))

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

    def to_neo_index(self, value):
        #for now, we'll just use a fixed-width binary decimal encoding with a
        #'-' for negative and '0' for positive or 0.
        s = str(abs(value))
        if len(s) > 20:
            raise ValueError('Values should be between {0} and {1}.'.format(MIN_INT, MAX_INT))
        return ('-' if value < 0 else '0') + s.zfill(19)

class AutoProperty(IntegerProperty):
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
        Return a Gremlin/Groovy closure that can compute next_value()
        server-side. The function should be named 'nextValue', and take a
        single value as an argument to increment.
        """
        script = \
        """
        def nextValue = { i -> i + 1}
        """
        return script

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

    def from_neo(self, value):
        if value is None or value == '':
            return None
        if isinstance(value, datetime.datetime):
            return value.date()
        if isinstance(value, datetime.date):
            return value

        return self.__parse_date_string(value)

    def to_neo(self, value):
        result = None

        if value is None:
            return ''
        if isinstance(value, datetime.datetime):
            result = value
        elif isinstance(value, datetime.date):
            result = value
        else:
            result = self.__parse_date_string(value)

        return self._format_date(result)

    def pre_save(self, model_instance, add, attname):
        if self.auto_now or (self.auto_now_add and add):
            value = datetime.date.today()
            setattr(model_instance, attname, value)
            return value
        else:
            return super(DateProperty, self).pre_save(model_instance, add, attname)

class DateTimeProperty(DateProperty):
    __format = '%Y-%m-%d %H:%M:%S.%f'

    default_error_messages = {
        'invalid': _(u'Enter a valid date/time in YYYY-MM-DD HH:MM[:ss[.uuuuuu]] format.'),
    }

    MAX=datetime.datetime.max
    MIN=datetime.datetime.min

    @classmethod
    def _format_datetime(cls, value, format_string=None):
        if not format_string:
            format_string = cls.__format
        time_string = cls._format_date(value, format_string)
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

    def from_neo(self, value):
        if value is None or value == '':
            return None

        if isinstance(value, datetime.datetime):
            return value
        if isinstance(value, datetime.date):
            return datetime.datetime(value.year, value.month, value.day)

        return self.__parse_datetime_string(value)

    def to_neo(self, value):
        result = None

        if value is None:
            return ''
        if isinstance(value, datetime.datetime):
            result = value
        elif isinstance(value, datetime.date):
            result = datetime.datetime(value.year, value.month, value.day)
        else:
            result = self.__parse_datetime_string(value)

        return self._format_datetime(result)

    def pre_save(self, model_instance, add, attname):
        if self.auto_now or (self.auto_now_add and add):
            value = datetime.datetime.now()
            setattr(model_instance, attname, value)
            return value
        else:
            return super(DateTimeProperty, self).pre_save(model_instance, add, attname)


class DateTimeTZProperty(DateTimeProperty):
    '''
    DateTimeProperty that can store and retrieve timezone-aware datetimes.
    '''
    __format = '%Y-%m-%d %H:%M:%S.%f %z'

    @classmethod
    def _format_offset(cls, offset_timedelta):
        '''
        Produce a timezone offset string (+/- HHMM) from a timedelta.
        '''
        try:
            seconds = offset_timedelta.total_seconds()
        except AttributeError:
            # total_seconds method is only available from 2.7 up
            td = offset_timedelta
            days_to_secs = td.days * 24 * 3600.0
            secs_to_micro = (td.seconds + days_to_secs) * (10 ** 6)
            seconds = (td.microseconds + secs_to_micro) / (10 ** 6)
        mins = seconds / 60
        hrs = mins / 60
        mins = mins % 60
        return '%+03d%02d' % (hrs, mins)

    @classmethod
    def _parse_tz(cls, tz_str=None):
        '''
        Read a timezone string of the form '+0000' and return a timezone
        object. If not given, just return UTC.
        '''
        tz_str = tz_str.strip() if tz_str else ''  # Ensure no whitespace
        if (not tz_str) or (tz_str == '+0000') or (tz_str == '-0000'):
            # Shortcut for the common case where it's UTC, or default
            return tzutc()
        # Otherwise, pull out the hours and minutes and construct a
        # tzoffset(), which requires an offset in seconds
        hrs = int(tz_str[1:3])
        mins = int(tz_str[3:5])
        mult = -1 if (tz_str[0] == '-') else 1
        offset = mult * ((hrs * 3600) + (mins * 60))
        return tzoffset('LOCAL', offset)

    @classmethod
    def _format_datetime_with_tz(cls, value):
        '''
        Format a datetime (e.g. for storage in Neo4j) with a timezone offset
        appended as +/- HHMM.
        '''
        formatted = cls._format_datetime(value, cls.__format)
        if value.utcoffset() is not None:
            offset_string = " " + cls._format_offset(value.utcoffset())
        else:
            offset_string = ""
        return formatted.replace("%z", offset_string).strip()

    @classmethod
    def __parse_datetime_string_with_tz(cls, value):
        '''
        Parse a stringified datetime into a datetime object, first trying to
        read a timezone (if one is provided in our format). Uses the superclass
        method to parse the actual string, and adds any timezone information
        at the end.
        '''
        try:
            # Try converting with timezone offset. Since strptime is decidedly
            # inconsistent with support for '%z', this must be done manually:
            # if a '+HHMM' is present, it'll form the last five characters of
            # the string
            dt_val, tz_str = value[:-5], value[-5:]
            dt_val = dt_val.strip()  # ensure no trailing whitespace
            tz_info = cls._parse_tz(tz_str)
        except ValueError:
            tz_info = None
            dt_val = value
        # HACK: Relies on CPython 2.x's double-underscore name-mangling!
        dt = cls._DateTimeProperty__parse_datetime_string(dt_val)
        return dt.replace(tzinfo=tz_info)

    def from_neo(self, value):
        if value is None or value == '':
            return None
        if isinstance(value, datetime.datetime):
            return value
        if isinstance(value, datetime.date):
            return datetime.datetime(value.year, value.month, value.day)

        return self.__parse_datetime_string_with_tz(value)

    def to_neo(self, value):
        result = None

        if value is None:
            return ''
        if isinstance(value, datetime.datetime):
            result = value
        elif isinstance(value, datetime.date):
            result = datetime.datetime(value.year, value.month, value.day)
        else:
            result = self.__parse_datetime_string_with_tz(value)

        return self._format_datetime_with_tz(result)


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

    def get_default(self):
        return []

    #def from_neo(self, value):
    #    from nose.tools import set_trace; set_trace()
    #    if value is None:
    #        return []
    #    return value

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
