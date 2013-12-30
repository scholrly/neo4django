import itertools

from abc import ABCMeta
from collections import defaultdict
from threading import local

from django.core.exceptions import ImproperlyConfigured
from django.utils.importlib import import_module

from neo4django.decorators import transactional
from neo4django.neo4jclient import EnhancedGraphDatabase


class StubbornDict(dict):
    """
    A subclass of dict that enforces a strict set of keys. If an attempt
    is made to set an item with a key that belongs to a set of blacklisted
    or "stubborn" keys, no action is taken
    """

    def __init__(self, stubborn_keys, d):
        self._stubborn_keys = stubborn_keys
        super(StubbornDict, self).__init__(d)

    def __setitem__(self, key, value):
        if key in self._stubborn_keys:
            return
        return super(StubbornDict, self).__setitem__(key, value)

def copy_func(func):
    """
    Return a copy of a function with a shallow copy of the original's
    func_globals.
    """
    import types
    return types.FunctionType(func.func_code, dict(func.func_globals),
                              name=func.func_name, argdefs=func.func_defaults,
                              closure=func.func_closure)

def sliding_pair(seq):
    """
    Return a sliding window of size 2 over the given sequence. The last pair
    will include None, so that special action can be taken at the end of the
    sequence.
    """
    s1, s2 = itertools.tee(seq)
    s2.next()  # This ensures we get a None sentinel for the end of the iterator
    return itertools.izip_longest(s1, s2)


def uniqify(seq):
    """
    Returns a list of only unique items in `seq` iterable. This has the effect
    of preserving the original order of `seq` while removing ignoring duplicates.
    """
    seen = set()
    return [x for x in seq if x not in seen and not seen.add(x)]


def not_none(it):
    return itertools.ifilter(None, it)


def Enum(*enums, **other_enums):
    """
    Creates an enum-like type that sets attributes with corresponding 0-indexed integer
    values coming from positional arguments that are converted to uppercase. For example::

        >>> e = Enum('foo', 'bar')
        >>> e.FOO
        0
        >>> e.BAR
        1

    If keyword arguments are passed, the effect is the same, however the keyword value
    will represent the value of the enum attribute, rather than a 0-indexed integer.
    For example::

        >>> e = Enum(foo='bar', baz='qux')
        >>> e.FOO
        'bar'
        >>> e.BAZ
        'qux'
    """
    # Handle args that should be numeric. Swap enumerate idx and value for dict comprehension later
    numerical_items = itertools.starmap(lambda i, v: (str(v).upper(), i), enumerate(enums))

    # Handle keyword arguments
    keyword_items = itertools.starmap(lambda k, v: (str(k).upper(), v), other_enums.iteritems())

    # Chain all items
    all_items = itertools.chain(numerical_items, keyword_items)

    return type('Enum', (), dict(x for x in all_items))


def all_your_base(cls, base):
    """
    Generator for returning all the common base classes of `cls` that are subclasses
    of `base`. This will yield common bases of `cls` as well as any common bases
    of all of the ancestors of `cls`. For example, given the classes::

        >>> class A(object): pass
        >>> class B(A): pass
        >>> class C(B): pass
        >>> class D(object): pass
        >>> class E(C, D): pass

    Would yield::

        >>> [cls for cls in all_your_base(C, A)]
        [C, B, A]

        >>> [cls for cls in all_your_base(E, B)]
        [E, C, B]
    """
    if issubclass(cls, base):
        yield cls
        for parent in cls.__bases__:
            for cls in all_your_base(parent, base):
                yield cls


def write_through(obj):
    """
    Returns the value of `obj._meta.write_through`. Defaults to False
    """
    return getattr(getattr(obj, '_meta', None), 'write_through', False)


def buffer_iterator(constructor, items, size=1):
    """
    Generator that yields the result of calling `constructor` with each
    value of `items` as an argument. However, this is done in chunks
    of at most `size` items
    For example::

        >>> list(buffer_iterator(lambda x: x**2, range(5), size=2))
        [0, 1, 4, 9, 16]]
    """
    iteritems = iter(items)

    while True:
        for item in apply_to_buffer(constructor, iteritems, size):
            yield item


@transactional
def apply_to_buffer(constructor, items, size=1):
    """
    Calls `constructor` with at the first `size` values from an
    iterator `items`. Returns a list of return values from these
    calls, raising StopIteration if no calls were made.
    """
    result = [constructor(x) for x in itertools.islice(items, size)]

    if not result:
        raise StopIteration

    return result


def countdown(number):
    """
    A method that returns a new method that will return True `number` amount
    of times and return False from then on.
    """
    counter = itertools.count()

    def done(*junk):
        for count in counter:
            return count < number
    return done


class AssignableList(list):
    """
    A special subclass of list the allow setting of arbitrary object
    attributes. The python builtin list prevents this behavior by raising
    an AttributeError::

        >>> x = []
        >>> x.foo = 'bar'
        Traceback (most recent call last):
        ...
        AttributeError: 'list' object has not attribute 'foo'

    Alternatively::

        >>> x = AssignableList()
        >>> x.foo = 'bar'
        >>> x.foo
        'bar'
    """

    def __init__(self, *args, **kwargs):
        super(AssignableList, self).__init__(*args, **kwargs)
        self._new_attrs = {}

    def __setattr__(self, name, value):
        if name != '_new_attrs':
            self._new_attrs[name] = value
        super(AssignableList, self).__setattr__(name, value)

    def get_new_attrs(self):
        """
        Returns a copy of all attributes that have been assigned to this object
        """
        return self._new_attrs.copy()


class AttrRouter(object):
    """
    Black magic ;). This abstract class exists to prevent one of my least
    favorite code repetition scenarios, namely

    class CoolOwner(object):
        def __init__(self):
            self.member = ImportantMember()

        def a(self):
            return self.member.a()

        def b(self, *args, **kwargs):
            return self.member.b(*args, **kwargs)

        @property
        def c(self):
            return self.member.c()
        ...

    Ad infinitum. Instead, try this

    class CoolOwner(SomeParent, AttrRouter):
        def __init__(self):
            self.member = ImportantMember()
            self._route_all(['a','b'], self.member)
            self._route(['c'],self.member)

    And we're done. All attribute calls for 'a' and 'b'  will be routed to
    self.member- gets, sets, and deletes. Only gets for 'c' will be routed to
    self.member.

    "But what if you want to add a bit of functionality?" you might whine. It's
    alright, I did too.

    class CoolOwner(SomeParent, AttrRouter):
        def __init__(self):
            self.member = ImportantMember()
            self._route_all(['a','b'], self.member)
            self._route(['c'],self.member)

        def b(self, *args, **kwargs):
            if 'DEBUG' in kwargs:
                print 'DEBUG STATEMENT!'
                del kwargs['DEBUG']
            super(CoolOwner, self).b(*args, **kwargs)

    And you're set.

    This approach won't work for special methods, like __len__- I haven't tested
    which cause problems. If there's another attribute with the same name in the
    inheritance heirarchy as a routed attribute, and comes up before AttrRouter
    in the MRO, it will be used, instead- this was an intentional decision.

    Obviously (or maybe not), if you set self.member to another object, calls
    will still be routed to the original. Unroute, or route to a new object,
    before doing that. In the future, I'll try to support that use case.

    I came up with this to solve a pain point, but I might be missing something.
    Forgive me if there's a more natural solution, and let me know!
    - Matt Luongo, mhluongo 'at' g mail.com
    """
    #TODO use weakrefs in the router dictionary
    #TODO allow specifying a base object and then a string attribute to support
    #the case where routing to self.member, where member changes frequently-
    #eg self._route(['method1'], self, member_chain = ['member'])
    __metaclass__ = ABCMeta
    __router_dict_key = '_AttrRouter__attr_route_dict'

    @property
    def _key(self):
        """
        Returns the key used to locate get/set/del routings from the object __dict__
        """
        return AttrRouter.__router_dict_key

    @property
    def _router(self):
        """
        Returns the attr router stored in the object __dict__ with key
        `self._key`. If the key does not exist, it is initialized with
        a defaultdict
        """
        return self.__dict__.setdefault(self._key, defaultdict(dict))

    def __getattr__(self, name):
        """
        Gets the routed attribute named `name`. If not routed, the default
        attribute of the class is returned
        """
        target = self._router['get'].get(name, super(AttrRouter, self))
        return getattr(target, name)

    def __setattr__(self, name, value):
        """
        Sets the routed attribute named `name` to `value`. If not routed, the
        class defers to super's __setattr__
        """
        #remember, getattr and setattr don't work the same way
        if name in self._router['set']:
            return setattr(self._router['set'][name], name, value)
        return super(AttrRouter, self).__setattr__(name, value)

    def __delattr__(self, name):
        """
        Deletes the routed attribute named `name` to `value`. If not routed, the
        class defers to super's __delattr__
        """
        router = self._router['del']

        if name in router:
            return delattr(router[name], name)
        return super(AttrRouter, self).__delattr__(name)

    def _build_dict_list(self, get=True, set=False, delete=False):
        """
        Constructs a list of all routed attribute dicts for get/set/del
        indicated by keyword arguments `get`, `set`, `delete`
        """
        dicts = []

        if set:
            dicts.append(self._router['set'])

        if get:
            dicts.append(self._router['get'])

        if delete:
            dicts.append(self._router['del'])

        return dicts

    def _route(self, attrs, obj, get=True, set=False, delete=False):
        """
        Routes `attrs` to `obj` for get/set/del operations indicated by
        keyword boolean args `get`, `set`, and `delete`.
        """
        for d in self._build_dict_list(get=get, set=set, delete=delete):
            for attr in attrs:
                d[attr] = obj

    def _unroute(self, attrs, get=True, set=False, delete=False):
        """
        Removes `attrs` routed to `obj` from  routing lists get/set/del
        indicated by keyword boolean args `get`, `set`, and `delete`.
        """
        for d in self._build_dict_list(get=get, set=set, delete=delete):
            for attr in itertools.ifilter(lambda x: x in d, attrs):
                del d[attr]

    def _route_all(self, attrs, obj):
        """
        Routes `attrs` to `obj` for get/set/del operations
        """
        self._route(attrs, obj, get=True, set=True, delete=True)

    def _unroute_all(self, attrs, obj):
        """
        Removes `attrs` routed to `obj` from  routing lists get/set/del
        """
        self._unroute(attrs, obj, get=True, set=True, delete=True)


class Neo4djangoIntegrationRouter(object):
    """
    A django database router that will allow integration of both Neo4j and other
    RDBMS backends. This will make sure that django apps that rely on the traditional
    ORM will play nicely with Neo4j models
    """

    def _is_node_model(self, obj):
        """
        Checks if `obj` is a subclass of NodeModel. If `obj` is not a class
        type, a check if it is an instance of NodeModel is done instead.
        """
        # Imported here for circular imports
        from neo4django.db.models import NodeModel

        try:
            return issubclass(obj, NodeModel)
        except TypeError:
            return isinstance(obj, NodeModel)

    def allow_relation(self, obj1, obj2, **hints):
        """
        Checks if a relation between `obj1` and `obj2` should be allowed. This
        is done by checking that both objects are either NodeModels or regular
        django models
        """
        if self._is_node_model(obj1) != self._is_node_model(obj2):
            return False
        return None

    def allow_syncdb(self, db, model):
        """
        Checks if `model` class should be synced to `db`. This is always False
        for NodeModels
        """
        if self._is_node_model(model):
            return False
        return None


## TODO: I think this connection stuff  might belong elsewhere?
class ConnectionDoesNotExist(Exception):
    pass


def load_client(client_path):
    """
    Imports a custom subclass of `neo4django.neo4jclient.EnhancedGraphDatabase`. The
    only param `client_path` should be an importable python path string in the form
    `foo.bar.baz`. This method will raise an `ImproperlyConfigured` if a) the module/class
    cannot be import or the imported class is not a subclass of `EnhancedGraphDatabase`.
    """

    client_modname, client_classname = client_path.rsplit('.', 1)

    try:
        client_mod = import_module(client_modname)
    except ImportError:
        raise ImproperlyConfigured("Could not import %s as a client" % client_path)

    try:
        client = getattr(client_mod, client_classname)
    except AttributeError:
        raise ImproperlyConfigured("Neo4j client module %s has no class %s" %
                                   (client_mod, client_classname))

    if not issubclass(client, EnhancedGraphDatabase):
        raise ImproperlyConfigured("%s is not a subclass of EnhancedGraphDatabase" % client_path)

    return client


class ConnectionHandler(object):
    """
    This is copied straight from django.db.utils. It uses threadlocality
    to handle the case where the user wants to change the connection
    info in middleware -- by keeping the connections thread-local, changes
    on a per-view basis in middleware will not be applied globally.

    The only difference is whereas the django ConnectionHandler operates on various
    expected values of the DATABASES setting, this class operates with expected
    configurations for Neo4j connections
    """

    def __init__(self, databases):
        self.databases = databases
        self._connections = local()

    def ensure_defaults(self, alias):
        """
        Puts the defaults into the settings dictionary for a given connection
        where no settings is provided.
        """
        try:
            conn = self.databases[alias]
        except KeyError:
            raise ConnectionDoesNotExist("The connection %s doesn't exist" % alias)

        conn.setdefault('CLIENT', 'neo4django.neo4jclient.EnhancedGraphDatabase')
        if conn['CLIENT'] == 'django.db.backends.' or not conn['CLIENT']:
            conn['CLIENT'] = 'neo4django.neo4jclient.EnhancedGraphDatabase'
        conn.setdefault('OPTIONS', {})
        if 'HOST' not in conn or 'PORT' not in conn:
            raise ImproperlyConfigured('Each Neo4j database configured needs a configured host and port.')

        for setting in ['HOST', 'PORT']:
            conn.setdefault(setting, '')

        # TODO: We can add these back in if we upgrade to supporting 1.6
        # for setting in ['USER', 'PASSWORD']:
        #     conn.setdefault(setting, None)

    def __getitem__(self, alias):
        if hasattr(self._connections, alias):
            return getattr(self._connections, alias)

        self.ensure_defaults(alias)
        db = self.databases[alias]
        Client = load_client(db['CLIENT'])
        conn = Client('http://%s:%d%s' % (db['HOST'], db['PORT'], db['ENDPOINT']), **db['OPTIONS'])
        setattr(self._connections, alias, conn)

        return conn

    def __setitem__(self, key, value):
        setattr(self._connections, key, value)

    def __iter__(self):
        return iter(self.databases)

    def __repr__(self):
        return "<ConnectionHandler(%s)>" % str(self.databases)

    def all(self):
        return [self[alias] for alias in self]
