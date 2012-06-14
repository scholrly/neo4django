
from django.db import models
from django.db.models.fields.related import add_lazy_relation
from django.db.models.query_utils import DeferredAttribute
from django.db.models.query import EmptyQuerySet

from neo4django import Incoming, Outgoing
from neo4django.db import DEFAULT_DB_ALIAS
from neo4django.decorators import not_implemented, transactional
from neo4django.utils import buffer_iterator, AssignableList, AttrRouter
from neo4django.constants import INTERNAL_ATTR, ORDER_ATTR
from base import NodeModel
from neo4django.db.models.query import conditions_from_kws, matches_condition


from neo4jrestclient.constants import RELATIONSHIPS_ALL, RELATIONSHIPS_IN, RELATIONSHIPS_OUT

from collections import defaultdict

class LazyModel(object):
    """
    A proxy class that enables relationships to have str targets, eg

        actors = Relationship('Actor', ...)
    """
    def __init__(self, cls, field, name, setup_reversed):
        self.__cls = cls
        self.__field = field
        self.__name = name
        self.__setup_reversed = setup_reversed
        add_lazy_relation(cls, field, name, self.__setup)

    def __setup(self, field, target, source):
        if not issubclass(target, NodeModel):
            raise TypeError("Relationships may only extend from Nodes.")
        self.__target = target
        self.__setup_reversed(target)
    __target = None

    @property
    def __model(self):
        model = self.__target
        if model is None:
            raise ValueError("Lazy model not initialized!")
        else:
            return model

    def __getinitargs__(self):
        return (self.__cls, self.__field, self.__name, self.__setup_reversed)

    def __getattr__(self, attr):
        if attr in ('__deepcopy__',) and self.__target is None:
            raise AttributeError
        return getattr(self.__model, attr)

    def __call__(self, *args, **kwargs):
        return self.__model(*args, **kwargs)

class RelationshipBase(type):
    """
    Metaclass for Relationships. Creates a RelationshipModel for each Relationship
    subclass that extends the models of all Relationship superclasses. 
    """
    def __new__(meta, name, bases, body):
        new = super(RelationshipBase, meta).__new__
        parents = [cls for cls in bases if isinstance(cls, RelationshipBase)]
        if not parents: # this is the base class
            return new(meta, name, bases, body)
        module = body.pop('__module__')
        modelbases = [cls.Model for cls in parents
                        if hasattr(cls, 'Model')]
        Model = RelationshipModel.new(module, name, modelbases)
        for key, value in body.items():
            if hasattr(value, 'contribute_to_class'):
                value.contribute_to_class(Model, key)
            else:
                setattr(Model, key, value)
        return new(meta, name, bases, {
                '__module__': module,
                'Model': Model,
            })

    #TODO not necessary until we have relationship models, and leads to
    #recursion bug
    #def __getattr__(cls, key):
    #    if hasattr(cls, 'Model'):
    #        return getattr(cls.Model, key)
    #    else:
    #        raise AttributeError(key)
    #def __setattr__(cls, key, value):
    #    if hasattr(cls, 'Model'):
    #        setattr(cls.Model, key, value)
    #    else:
    #        raise TypeError(
    #            "Cannot assign attributes to base Relationship")

class RelationshipModel(object):
    """
    Model backing all relationships. Intended for a single instance to
    correspond to an edge in the graph.
    """
    __relationship = None

    def __init__(self):
        pass

    @property
    def relationship(self):
        rel = self.__relationship
        if rel is None:
            # XXX: better exception
            raise ValueError("Unsaved objects have no relationship.")
        return rel
    _neo4j_underlying = relationship

    @classmethod
    def new(RelationshipModel, module, name, bases):
        return type(name, bases + [RelationshipModel], {
                '__module__': module,})

    @not_implemented
    @classmethod
    def add_field(self, prop):
        raise NotImplementedError("<RelationshipModel>.add_field()")

class Relationship(object):
    """Extend to add properties to relationships."""
    __metaclass__ = RelationshipBase

    def __init__(self, target, rel_type=None, direction=None, optional=True,
                 single=False, related_single=False, related_name=None,
                 preserve_ordering=False, metadata={}, rel_metadata={},
                ):
        if direction is Outgoing:
            direction = RELATIONSHIPS_OUT
        elif direction is Incoming:
            direction = RELATIONSHIPS_IN
        elif direction is None:
            if not isinstance(rel_type, basestring):
                direction = rel_type.direction
            else:
                direction = RELATIONSHIPS_OUT
        if not isinstance(rel_type, basestring):
            if rel_type.direction != direction:
                raise ValueError("Incompatible direction!")
            rel_type = rel_type.type
        self.__target = target
        self.__name = rel_type
        self.__single = single
        self.direction = direction
        self.__related_single = related_single
        self._related_name = related_name
        self.__ordered = preserve_ordering
        self.__meta = metadata
        self.__related_meta = rel_metadata

    target_model = property(lambda self: self.__target)
    ordered = property(lambda self: self.__ordered)
    meta = property(lambda self: self.__meta)

    __is_reversed = False

    def reverse(self, target, name):
        if self.direction is RELATIONSHIPS_IN:
            direction = RELATIONSHIPS_OUT
        elif self.direction is RELATIONSHIPS_OUT:
            direction = RELATIONSHIPS_IN
        else:
            direction = RELATIONSHIPS_OUT
        relationship = Relationship(
            target, rel_type=self.__name, direction=direction,
            single=self.__related_single, related_name=name,
            metadata=self.__related_meta, preserve_ordering=self.__ordered)
        relationship.__is_reversed = True
        return relationship

    def reversed_name(self, target=None):
        if self._related_name:
            return self._related_name
        else:
            return self.get_name(target, self.__single)

    @staticmethod
    def get_name(target, single=False):
        suffix = '%ss' if single else '%s_set'
        if isinstance(target, basestring):
            name = target.rsplit('.',1)[-1]
        else:
            name = target.__name__
        return suffix % name.lower()

    def contribute_to_class(self, source, name):
        if not issubclass(source, NodeModel):
            raise TypeError("Relationships may only extend from Nodes.")
        self.creation_counter = source.creation_counter
        
        if hasattr(self, 'Model'):
            if self.__single:
                Bound = SingleRelationship
            else:
                Bound = MultipleRelationships
            bound = Bound(self, source, self.__name or name, name,
                            self.Model)
        else:
            if self.__single:
                Bound = SingleNode
            else:
                Bound = MultipleNodes
            bound = Bound(self, source, self.__name or name, name)
        source._meta.add_field(bound)
        if not hasattr(source._meta, '_relationships'):
            source._meta._relationships = {}
        source._meta._relationships[name] = bound
        setattr(source, name, bound)
        if isinstance(self.__target, basestring):
            self.__target = LazyModel(source, self, self.__target,
                        lambda target: bound._setup_reversed(target))
        target = self.__target
        if not self.__is_reversed:
            bound._setup_reversed(target)

    ###################
    # SPECIAL METHODS #
    ###################

    def __getinitargs__(self):
        return (self.__target, self.__name, self.direction, True, self.__single,
                self.__related_single, self.reversed_name, self.__ordered,
                self.__meta, self.__related_meta)

#subclasses DeferredAttribute to avoid being set to None in
#django.db.models.Model.__init__().
class BoundRelationship(AttrRouter, DeferredAttribute):
    indexed = False
    rel = None
    primary_key = False
    choices = None
    db_index = None

    def __init__(self, rel, source, relname, attname, serialize=True):
        self.__rel = rel
        self.__source = source
        self._type = relname
        self.__attname = attname
        self.serialize = serialize
        relationships = self._relationships_for(source)
        relationships[self.__attname] = self # XXX weakref
        self._route(['reversed_name',
                     'direction',
                     'target_model',
                     'ordered',
                     'meta',
                    ],self.__rel)

    def _setup_reversed(self, target):
        self.__target = target
        if not isinstance(target, LazyModel):
            self.__rel.reverse(self.__source,
                                self.__attname).contribute_to_class(
                target, self.reversed_name(self.__source))

    attname = name = property(lambda self: self.__attname)

    @property
    def rel_type(self):
        return self._type

    def get_default(self):
        return None

    def contribute_to_class(self, source, name):
        return self.__rel.contribute_to_class(source, name)

    def _get_val_from_obj(self, obj):
        return self.__get__(obj)

    def value_to_string(self, obj):
        return str(self.__get__(self))

    @staticmethod
    def _state_for(instance, create=True):
        try:
            state = instance.__state
        except:
            state = {}
            if create:
                instance.__state = state
        return state

    @staticmethod
    def _relationships_for(obj_or_cls):
        meta = obj_or_cls._meta
        try:
            relationships = meta._relationships
        except:
            meta._relationships = relationships = {}
        return relationships

    @staticmethod
    def _all_relationships_for(obj_or_cls):
        new_rel_dict = {}
        new_rel_dict.update(BoundRelationship._relationships_for(obj_or_cls))

        cls = obj_or_cls if isinstance(obj_or_cls, type) else type(obj_or_cls)

        for parent in cls.__bases__:
            if hasattr(parent, '_meta'):
                new_rel_dict.update(BoundRelationship._all_relationships_for(parent))

        return new_rel_dict

    def _save_(instance, node):
        state = BoundRelationship._state_for(instance, create=False)
        if state:
            rels = BoundRelationship._all_relationships_for(instance)
            for key in state.keys():
                rels[key]._save_relationship(instance, node, state[key])
                if isinstance(state[key], tuple): #HACK, rearchitect
                    state[key] = (False, state[key][1])
                    
    #TODO this... well, consider revising
    NodeModel._save_neo4j_relationships = staticmethod(_save_) 
    del _save_

    @not_implemented
    def _save_relationship(self, instance, node, state):
        pass

    def _load_relationships(self, node):
        #Returns all neo4j relationships attached to the provided neo4j node.
        #TODO TODO we can probably trash this function with the new backend, refs 174
        if self.direction is RELATIONSHIPS_OUT:
            rel_func = node.relationships.outgoing
        else:
            rel_func = node.relationships.incoming
        rels = rel_func([self._type])
        if not hasattr(self, '_relationships'):
            attrs = {}
        else:
            attrs = self._relationships.get_new_attrs()

        self._relationships = AssignableList(rels)
        for k in attrs.keys():
            setattr(self._relationships, k, attrs[k])
        return self._relationships

    creation_counter = property(lambda self:self.__rel.creation_counter)

    def _set_relationship(self, obj, state, value):
        if value is None: # assume initialization - ignore
            return # TODO: verify that obj is unsaved!
        raise TypeError("<%s>.%s is not assignable" %
                        (obj.__class__.__name__, self.name))

    def _del_relationship(self, obj, state):
        raise TypeError("Cannot delete <%s>.%s" %
                        (obj.__class__.__name__, self.name))

    def _create_neo_relationship(self, node, obj, **kwargs):
        neo_rel_attrs = kwargs.get('attrs', {})
        neo_rel_attrs[INTERNAL_ATTR] = True
        #TODO 'using' throughout relationships.py, #175
        if obj.pk is None:
            obj.save(using=obj.using)
        other = obj.node
        # TODO: verify that it's ok in the reverse direction?
        if self.direction != 'out':
            node, other = other, node

        node.relationships.create(self._type, other, **neo_rel_attrs)

    @classmethod
    def to_python(cls, value):
        """
        A python-centric alias for from_neo()
        """
        return cls.from_neo(value)

    @classmethod
    def from_python(cls, value):
        """
        A python-centric alias for to_neo()
        """
        return cls.to_neo(value)

    @classmethod
    def to_neo(cls, value):
        return value

    @classmethod
    def from_neo(cls, value):
        return value

    ###################
    # SPECIAL METHODS #
    ###################

    def __getinitargs__(self):
        return (self.__rel, self.__source, self.__type, self.__attname, self.serialize)

    def __cmp__(self, other):
        return cmp(self.creation_counter, other.creation_counter)

    ######################
    # DESCRIPTOR METHODS #
    ######################

    def __get__(self, obj, cls=None):
        if obj is None: return self
        return self._get_relationship(obj, self._state_for(obj))

    def __set__(self, obj, value):
        self._set_relationship(obj, self._state_for(obj), value)

    def __delete__(self, obj):
        self._del_relationship(obj, self._state_for(obj))

class SingleNode(BoundRelationship):
    #BoundRelationship subclass for a single node relationship without an
    #associated relationship model.
    def _get_relationship(self, obj, state):
        if self.name in state:
            changed, result = state[self.name]
            return result

        if hasattr(obj, 'node'):
            this = obj.node
        else:
            return None

        result = self._load_related(this)
        state[self.name] = False, result
        return result

    @transactional
    def _load_related(self, node):
        relationships = self._load_relationships(node)
        django_relationships = filter(lambda rel: rel['_neo4django'], relationships)
        if len(django_relationships) < 1:
            return None
        elif len(django_relationships) > 1:
            raise ValueError("There's an ambiguous relationship set in the "\
                             "database from node %d - there should only be one"\
                             " relationship flagged as '_neo4django' for a "\
                             "single=True Relationship." % node.id)
        return self._neo4j_instance(node, django_relationships[0])

    def _neo4j_instance(self, this, relationship):
        if this.id == relationship.end.id:
            that = relationship.start
        else:
            that = relationship.end #get the other node

        return self.target_model._neo4j_instance(that)

    def _del_relationship(self, obj, state):
        self._set_relationship(obj, state, None)

    def _set_relationship(self, obj, state, other):
        state[self.name] = True, other

    def _save_relationship(self, instance, node, state):
        changed, other = state
        if not changed: return
        rels = self._load_relationships(node)

        #delete old relationship
        #create new relationship

        if other is None:
            #delete old relationship if it exists
            if hasattr(rels, 'single') and rels.single:
                rels.single.delete() #TODO this deletion should be transactional w creation
            rels.single = None
        else:
            rels.single = self._create_neo_relationship(node, other)
            #other._save_neo4j_node(DEFAULT_DB_ALIAS)

    def _set_cached_relationship(self, obj, other):
        state = BoundRelationship._state_for(obj)
        if self.name in state and state[self.name]:
            raise ValueError("Can't set the cache on an already initialized relationship!")
        state[self.name] = False, other

class BoundRelationshipModel(BoundRelationship):
    def __init__(self, rel, cls, relname, attname, Model):
        super(BoundRelationship, self).__init__(
            rel, cls, relname, attname)
        self.Model = Model
        raise NotImplementedError("Support for extended relationship "
                                    "models is not yet implemented.")

class SingleRelationship(BoundRelationshipModel): # WAIT!
    @not_implemented
    def _get_relationship(self, obj, state):
        pass
    @not_implemented
    def _set_relationship(self, obj, state, other):
        pass

class MultipleNodes(BoundRelationship):
    #BoundRelationship subclass for a multi-node relationship without an
    #associated relationship model.

    def _get_relationship(self, obj, states):
        state = states.get(self.name)
        if state is None:
            states[self.name] = state = RelationshipInstance(self, obj)
        return state

    def _set_relationship(self, obj, state, value):
        if value is not None:
            if state.get(self.name) is None:
                state[self.name] = RelationshipInstance(self, obj)
            items = list(state[self.name].all())
            notsaved = state[self.name]._added
            if items and len(notsaved) < len(items):
                state[self.name].remove(*items) 
                #TODO: make it so it only removes authors not in value
                #      and remove authors already there from value
                #XXX: only works when removing from saved nodes
            if notsaved:
                notsaved[:] = [] #TODO give rel instance a method for this?
            if hasattr(value, '__iter__'):
                state[self.name].add(*value)
            else:
                state[self.name].add(value)

    def value_to_string(self, obj):
       return str([item.pk for item in list(self.__get__(obj).all())])

    def _neo4j_instance(self, this, relationship):
        if this.id == relationship.start.id:
            that = relationship.end
        else:
            that = relationship.start
        return self.target_model._neo4j_instance(that)

    def accept(self, obj):
        pass # TODO: implement verification

    def _save_relationship(self, instance, node, state):
        state.__save__(node)

   # @classmethod
   # def to_neo(cls, value):
   #      pass

   # @classmethod
   # def from_neo(cls, value):
   #     pass

    # def _set_relationship():
    #    #TODO diff rels and this list
    #    #add news rels, delete old
    #    #preserve order if we're supposed to
    #    pass

    def _load_relationships(self, node, ordered=False, **kwargs):
        sup = super(MultipleNodes, self)._load_relationships(node, **kwargs)
        if ordered:
            return sorted(sup, key=lambda rel:rel[ORDER_ATTR])
        return sup

    def _create_neo_relationship(self, node, *args, **kwargs):
        if self.ordered:
            rels = self._load_relationships(node, ordered=True, **kwargs)
            new_index = rels[-1][ORDER_ATTR] + 1 if len(rels) > 0 else 0
            if 'attrs' in kwargs:
                kwargs['attrs'][ORDER_ATTR]=new_index
            else:
                kwargs['attrs'] = { ORDER_ATTR:new_index }
        return super(MultipleNodes, self). \
                    _create_neo_relationship(node, *args, **kwargs)

class MultipleRelationships(BoundRelationshipModel): # WAIT!
    @not_implemented
    def _get_relationship(self, obj, state):
        pass
    @not_implemented
    def add(self, obj, other):
        pass

class RelationshipInstance(models.Manager):
    """
    A manager that keeps state for the many side (`MultipleNodes`) of relationships.
    """
    def __init__(self, rel, obj):
        self.__rel = rel
        self.__obj = obj
        self._added = [] # contains domain objects
        self._removed = [] # contains relationships
        #holds cached domain objects (that have been added or loaded by query)
        self._cache = []
        self._cache_unique = set([])
        # TODO: accessor for rel so we don't need .model or .name
        self.model = rel._BoundRelationship__rel._Relationship__target
        self.name = rel._BoundRelationship__rel._related_name

    ordered = property(lambda self: self.__rel.ordered)

    def _add_to_cache(self, *relationship_neo4j_pairs):
        for pair in relationship_neo4j_pairs:
            if pair not in self._cache_unique:
                self._cache.append(pair)
                self._cache_unique.add(pair)

    def _remove_from_cache(self, obj):
        for r, cached_obj in self._cache[:]:
            if cached_obj == obj:
                pair = (r, cached_obj)
                self._cache.remove(pair)
                self._cache_unique.remove(pair)
                break

    def __save__(self, node):
        #Deletes all relationships removed since last save and adds any new
        #relatonships to the database.
    
        #TODO this should be batched
        for relationship in self._removed:
            relationship.delete()
        for obj in self._added:
            new_rel = self.__rel._create_neo_relationship(node, obj)
            self._add_to_cache((new_rel, obj))
        self._removed[:] = []
        self._added[:] = []

    def _neo4j_relationships_and_models(self, node):
        "Returns generator of relationship, neo4j instance tuples associated with node."
        if not self._cache:
            self._add_to_cache(*[(r, self.__rel._neo4j_instance(node, r)) for r in 
                           self.__rel._load_relationships(node, ordered=self.ordered)])
        for tup in self._cache:
            if tup[0] not in self._removed:
                yield tup

    @property
    def _new(self):
        for item in self._added:
            yield item

    @property
    def _old(self):
        for item in self._removed:
            yield item

    def add(self, *objs):
        """
        Adds object(s) to the relationship. If ordered is True for
        the relationship, these objects will all be put at the end of the line.
        """
        for obj in objs:
            self.__rel.accept(obj)
        self._added.extend(objs)

    def remove(self, *objs):
        """
        Remove objects from the relationship. If ordered is True,
        remove the first relationship to this object- otherwise, remove one
        of the relationships indiscriminately.
        """
        rel = self.__rel
        if hasattr(self.__obj, 'node'):
            neo_rels = list(rel._load_relationships(self.__obj.node,
                                                    ordered=self.ordered))
            rels_by_node_id = defaultdict(list)
            for neo_rel in neo_rels:
                #import pdb; pdb.set_trace()
                other_end = neo_rel.start if neo_rel.end.id == self.__obj.node.id\
                            else neo_rel.end
                rels_by_node_id[other_end.id].append(neo_rel)
            nodes_to_remove = [o.node for o in objs if hasattr(o, 'node')]
            unsaved_obj_to_remove = [o for o in objs if not hasattr(o, 'node')]
            for obj in objs:
                candidate_rels = rels_by_node_id[obj.node.id]\
                        if hasattr(o, 'node') else []
                if candidate_rels:
                    if candidate_rels[0] not in self._removed:
                        self._removed.append(candidate_rels.pop(0))
                else:
                    try:
                        self._added.remove(obj)
                    except ValueError:
                        raise rel.target_model.DoesNotExist("%r is not related to %r." % (obj, self.__obj))
                self._remove_from_cache(obj)
        else:
            for obj in objs:
                try:
                    if obj in self._added:
                        self._added.remove(obj)
                    else:
                        self._remove_from_cache(obj)
                except ValueError:
                    raise rel.target_model.DoesNotExist("%r is not related to %r." % (obj, self.__obj))

    def clear(self):
        all_objs = list(self.all())
        self.remove(*all_objs)

    def clone(self):
        # Should cache be updated as well?
        cloned = RelationshipInstance(self.__rel, self.__obj)
        cloned.add(*self._new)
        return cloned

    def create(self, **kwargs):
        kwargs[self.name] = self.__obj
        new_model = self.model(**kwargs)
        self.new_model = new_model
        # TODO: saving twice, should only need
        # to save self.__obj after #89 fix
        new_model.save()
        self.__obj.save()

    @not_implemented
    def get_or_create(self, *args, **kwargs):
        pass

    def get_query_set(self):
        return RelationshipQuerySet(self, self.__rel, self.__obj)

    def get_empty_query_set(self):
        return EmptyQuerySet()

class RelationshipQuerySet(object):
    def __init__(self, inst, rel, obj):
        self.__inst = inst
        self.__rel = rel
        self.__obj = obj
        self.model = rel._BoundRelationship__rel._Relationship__target

    def filter(self, **kwargs):
        "Returns RelationshipQuerySet with filtered items"
        inst = self.__inst
        added = list(inst._new)

        new_inst = self.__inst.clone()
        new_inst.clear()

        if added:
            iterable = added
        else:
            # TODO: is wrapping in getattr necessary?
            node = getattr(self.__obj, 'node', None)
            iterable = [i for r, i in inst._neo4j_relationships_and_models(node)] if node else []

        for item in iterable:
            if any(matches_condition(item.node, c) for c in conditions_from_kws(self.model, kwargs)):
                new_inst.add(item)
        return RelationshipQuerySet(new_inst, self.__rel, self.__obj)

    def __saved_instances(self, node):
        for rel, item in self.__inst._neo4j_relationships_and_models(node):
            if self.__keep_relationship(rel) and self.__keep_instance(item):
                yield item

    def __iter__(self):
        removed = list(self.__inst._old)
        added = list(self.__inst._new)
        try:
            node = self.__obj.node
        except:
            pass
        else:
            for item in self.__saved_instances(node):
                yield item
        for item in added:
            if self.__keep_instance(item):
                yield item

    def __len__(self):
        return sum(1 for _ in self)

    # Taken from Django's QuerySet repr
    def __repr__(self):
        REPR_OUTPUT_SIZE = 4
        data = list(self[:REPR_OUTPUT_SIZE + 1])
        if len(data) > REPR_OUTPUT_SIZE:
            data[-1] = "...(remaining elements truncated)..."
        return repr(data)

    def __getitem__(self, key):
        return list(self)[key]

    def __keep_instance(self, obj):
        return True # TODO: filtering

    def __keep_relationship(self, rel):
        return True # TODO: filtering

    @not_implemented
    def get(self, **lookup):
        pass

    def count(self):
        return len(self)
