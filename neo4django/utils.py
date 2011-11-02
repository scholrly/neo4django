import itertools
from abc import ABCMeta

from decorators import transactional

def uniqify(seq):
    seen = set()
    return [x for x in seq if x not in seen and not seen.add(x)]

def Enum(*enums, **other_enums):
    enum_items = itertools.izip([str(e).upper() for e in enums],
                                itertools.count(0))
    enum_items = itertools.chain(enum_items, other_enums.items())
    return type('Enum', (), dict([(str(i[0]).upper(), i[1]) for i in enum_items]))

def all_your_base(cls, base):
    if issubclass(cls, base):
        yield cls
        for parent in cls.__bases__:
            for cls in all_your_base(parent, base):
                yield cls

def write_through(obj):
    return getattr(getattr(obj,'_meta',None),'write_through', False)

def buffer_iterator(constructor, items, size=1):
    items = iter(items) # make sure we have an iterator
    while 1:
        for item in apply_to_buffer(constructor, items, size):
            yield item

@transactional
def apply_to_buffer(constructor, items, size=1):
    result = [constructor(item) for item in
                itertools.takewhile(countdown(size), items)]
    if not result:
        raise StopIteration
    return result

def countdown(number):
    counter = itertools.count()
    def done(*junk):
        for count in counter:
            return count < number
    return done

class AssignableList(list):

    def __init__(self, *args, **kwargs):
        super(AssignableList, self).__init__(*args, **kwargs)
        self._new_attrs = {}

    def __setattr__(self, name, value):
        if name != '_new_attrs':
            self._new_attrs[name] = value
        super(AssignableList, self).__setattr__(name, value)

    def get_new_attrs(self):
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
    def __init__(self, *args, **kwargs):
        super(AttrRouter, self).__init__(*args, **kwargs)
        key = AttrRouter.__router_dict_key
        self.__dict__[key] = {'set':{},'del':{},'get':{}}

    def __getattr__(self, name):
        key = AttrRouter.__router_dict_key
        if not key in self.__dict__:
            self.__dict__[key] = {'set':{},'del':{},'get':{}}
        get_dict = self.__dict__[key]['get']
        if name in get_dict:
            return getattr(get_dict[name], name)
        return getattr(super(AttrRouter, self), name)

    def __setattr__(self, name, value):
        key = AttrRouter.__router_dict_key
        #remember, getattr and setattr don't work the same way
        if not key in self.__dict__:
            self.__dict__[key] = {'set':{},'del':{},'get':{}}
        set_dict = self.__dict__[key]['set']
        if name in set_dict:
            return setattr(set_dict[name], name, value)
        return super(AttrRouter, self).__setattr__(name, value)

    def __delattr__(self, name):
        key = AttrRouter.__router_dict_key
        if not key in self.__dict__:
            self.__dict__[key] = {'set':{},'del':{},'get':{}}
        del_dict = self.__dict__[key]['del']
        if name in del_dict:
            return delattr(del_dict[name], name)
        return super(AttrRouter, self).__delattr__(name, value)

    def _route(self, attrs, obj, get=True, set=False, delete=False):
        key = AttrRouter.__router_dict_key
        if not key in self.__dict__:
            self.__dict__[key] = {'set':{},'del':{},'get':{}}
        router = self.__dict__[key]
        dicts = []
        if set:
            dicts.append(router['set'])
        if get:
            dicts.append(router['get'])
        if delete:
            dicts.append(router['del'])
        for attr in attrs:
            for d in dicts:
                d[attr] = obj

    def _unroute(self, attrs, get=True, set=False, delete=False):
        key = AttrRouter.__router_dict_key
        if not key in self.__dict__:
            self.__dict__[key] = {'set':{},'del':{},'get':{}}
        router = self.__dict__[key]
        dicts = []
        if set:
            dicts.append(router['set'])
        if get:
            dicts.append(router['get'])
        if delete:
            dicts.append(router['del'])
        for attr in attrs:
            for d in dicts:
                if attr in d:
                    del d[attr]

    def _route_all(self, attrs, obj):
        self._route(attrs, obj, get=True, set=True, delete=True)

    def _unroute_all(self, attrs, obj):
        self._unroute(attrs, obj, get=True, set=True, delete=True)

class Neo4djangoIntegrationRouter():
    def allow_relation(self, obj1, obj2, **hints):
        "Disallow any relations between Neo4j and regular SQL models."
        from neo4django.db.models import NodeModel
        def type_test(o):
            return issubclass(o, NodeModel) if isinstance(o, type) else isinstance(o, NodeModel)
        a, b = (type_test(o) for o in (obj1, obj2))
        if a != b:
            return False
        return None

    def allow_syncdb(self, db, model):
        "No Neo4j models should ever be synced."
        from neo4django.db.models import NodeModel
        if issubclass(model, NodeModel):
            return False
        return None
